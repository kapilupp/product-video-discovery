"""
Runs one full search end to end. Kicked off as a background task by the
/api/search endpoint so the HTTP request returns immediately with a
search_id; the frontend then follows progress over SSE and polls/reads the
final result via GET /api/search/:id.
"""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.search import Search, SearchStatus
from app.models.video import Video
from app.services import dedup, image_brain, progress_bus
from app.services.product_extractor import extract_product
from app.services.sources.base import RawVideo, RequestBudget, SourceError
from app.services.sources.instagram import search_instagram_reels
from app.services.sources.instagram_browser import search_instagram_reels_via_browser
from app.services.sources.meta_ad_library import search_meta_ad_library
from app.services.sources.tiktok import search_tiktok

MIN_PER_SOURCE = 20
MAX_TOPUP_ROUNDS = 3


async def run_search_pipeline(search_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        search = await db.get(Search, search_id)
        try:
            await _run(db, search)
        except Exception as exc:  # noqa: BLE001 - top-level guard for the whole job
            search.status = SearchStatus.failed
            search.error_message = str(exc)
            await db.commit()
            await progress_bus.publish(search_id, {"stage": "failed", "message": str(exc)})
        finally:
            await progress_bus.close(search_id)
            progress_bus.cleanup(search_id)


async def _emit(search_id: uuid.UUID, stage: str, **extra) -> None:
    await progress_bus.publish(search_id, {"stage": stage, **extra})


async def _run(db: AsyncSession, search: Search) -> None:
    search_id = search.id
    # An uploaded photo (SearchCreateRequest.image_base64) is stored as
    # product_image_url at creation time (see api/routes/search.py). It's
    # the user's own reference photo, so it takes priority over whatever
    # image scraping the product page finds.
    uploaded_image_url = search.product_image_url

    # 1. Resolve product context (from URL, or straight from the typed query)
    if search.product_url:
        await _emit(search_id, "extracting_product")
        search.status = SearchStatus.extracting_product
        await db.commit()

        product = await extract_product(search.product_url)
        search.product_title = product["title"] or search.product_url
        search.product_description = product["description"]
        if not uploaded_image_url:
            search.product_image_url = product["image_url"]
    else:
        search.product_title = search.query_text

    await db.commit()

    # 2. Image brain: attributes -> queries/hashtags (only if we have a photo)
    queries = [search.query_text] if search.query_text else []
    hashtags: list[str] = []

    if search.product_image_url:
        await _emit(search_id, "analyzing_image")
        search.status = SearchStatus.analyzing_image
        await db.commit()

        attributes = await image_brain.analyze_product_image(search.product_image_url)
        search.product_attributes = attributes
        queries = list(dict.fromkeys(queries + attributes.get("search_queries", [])))
        hashtags = attributes.get("hashtags", [])
        await db.commit()

    if not queries:
        queries = [search.product_title or "product"]

    # 3. Fetch all sources in parallel; each is isolated so one failing
    #    source never breaks the others (assignment requirement). Each one
    #    also tops itself up with widened queries/deeper pagination if the
    #    first pass would leave fewer than MIN_PER_SOURCE *new* videos.
    await _emit(search_id, "fetching_sources")
    search.status = SearchStatus.fetching_instagram
    await db.commit()

    results = await asyncio.gather(
        _safe_fetch(
            "instagram", search_id, lambda: _collect_with_topup(db, "instagram", queries, hashtags)
        ),
        _safe_fetch("meta", search_id, lambda: _collect_with_topup(db, "meta", queries, hashtags)),
        _safe_fetch("tiktok", search_id, lambda: search_tiktok(queries[0])),
    )
    instagram_raw, meta_raw, tiktok_raw = results

    # 4. De-dup within this batch, then annotate (not drop) against global
    #    history so "seen before" videos stay available via the UI toggle.
    await _emit(search_id, "deduping")
    all_raw: list[RawVideo] = instagram_raw + meta_raw + tiktok_raw
    candidates = []
    for video in all_raw:
        key = dedup.dedup_key_for(video)
        phash = await dedup.perceptual_hash(video.thumbnail_url)
        candidates.append((video, key, phash))

    unique_batch = dedup.dedup_within_batch(candidates)
    annotated = await dedup.annotate_seen(db, unique_batch)

    # 5. Score each surviving video against the product photo
    await _emit(search_id, "scoring")
    search.status = SearchStatus.scoring
    await db.commit()

    scored_rows: list[Video] = []
    for video, key, phash, seen_before in annotated:
        score, reason = 0, "No product image supplied; showing keyword matches unscored."
        if search.product_image_url and video.thumbnail_url:
            score, reason = await image_brain.score_video_frame(search.product_image_url, video.thumbnail_url)

        if score < settings.match_score_threshold and search.product_image_url:
            continue  # below threshold: discard per assignment spec

        scored_rows.append(
            Video(
                search_id=search_id,
                platform=video.platform,
                dedup_key=key,
                perceptual_hash=phash,
                video_url=video.video_url,
                thumbnail_url=video.thumbnail_url,
                caption=video.caption,
                match_score=score,
                match_reason=reason,
                seen_before=seen_before,
            )
        )

    db.add_all(scored_rows)
    await dedup.mark_new_as_seen(db, search_id, annotated)

    # The 20-per-source minimum is about *new* videos the user hasn't seen;
    # previously-seen ones are stored and viewable via the toggle but don't
    # count towards it.
    search.instagram_count = sum(1 for v in scored_rows if v.platform == "instagram" and not v.seen_before)
    search.meta_count = sum(1 for v in scored_rows if v.platform == "meta" and not v.seen_before)
    search.tiktok_count = sum(1 for v in scored_rows if v.platform == "tiktok" and not v.seen_before)
    search.status = SearchStatus.done
    await db.commit()

    await _emit(
        search_id,
        "done",
        instagram_count=search.instagram_count,
        meta_count=search.meta_count,
        tiktok_count=search.tiktok_count,
    )

    for shortfall_platform, count in (("instagram", search.instagram_count), ("meta", search.meta_count)):
        if count < MIN_PER_SOURCE:
            await _emit(
                search_id,
                "shortfall",
                platform=shortfall_platform,
                message=(
                    f"Only found {count}/{MIN_PER_SOURCE} new, usable {shortfall_platform} videos "
                    "after widening the query, de-duplication and scoring. Try a broader product "
                    "name, or check back later as new content is indexed."
                ),
            )


async def _collect_with_topup(
    db: AsyncSession, platform: str, queries: list[str], hashtags: list[str]
) -> list[RawVideo]:
    """
    Fetches raw videos for one source, then widens the query set and
    paginates deeper for up to MAX_TOPUP_ROUNDS if de-duplicating against
    history would leave fewer than MIN_PER_SOURCE new videos -- the
    assignment's "fetch more using expanded queries ... until the minimum
    is met" requirement.
    """
    collected: dict[str, RawVideo] = {}
    round_queries = list(queries)
    # One budget shared across every round below -- a fresh budget per round
    # would let MAX_TOPUP_ROUNDS multiply the effective per-search request
    # cap, which a tight-quota free tier (e.g. 20 requests/month) can't
    # absorb.
    budget_limit = (
        settings.instagram_max_requests_per_search
        if platform == "instagram"
        else settings.meta_max_requests_per_search
    )
    budget = RequestBudget(budget_limit)
    # Shared across rounds so a widening round that lands on the same term
    # as an earlier round doesn't spend another request re-fetching it.
    already_tried: set[str] = set()

    for round_num in range(MAX_TOPUP_ROUNDS):
        if budget.remaining <= 0:
            break
        max_pages = 3 + round_num * 2  # paginate deeper on each retry round

        if platform == "instagram":
            if settings.instagram_provider == "browser":
                # No request quota to protect here (it's our own headless
                # browser, not a metered third-party API), so no budget arg.
                raw = await search_instagram_reels_via_browser(round_queries, hashtags, MIN_PER_SOURCE, max_pages)
            else:
                raw = await search_instagram_reels(
                    round_queries, hashtags, MIN_PER_SOURCE, max_pages, budget, already_tried
                )
        elif platform == "meta":
            raw = await search_meta_ad_library(
                round_queries, MIN_PER_SOURCE, max_pages, budget, already_tried
            )
        else:
            raise ValueError(f"Unknown topup platform: {platform}")

        for video in raw:
            collected.setdefault(dedup.dedup_key_for(video), video)

        candidates = [(v, k, None) for k, v in collected.items()]
        new_count = await dedup.estimate_new_count(db, candidates)
        if new_count >= MIN_PER_SOURCE:
            break

        # Widen: fall back to broader/singular terms so the next round casts
        # a wider net instead of repeating the same exact-phrase queries.
        broader = [q.split()[-1] for q in queries if q]  # last word of each query, e.g. just "t-shirt"
        round_queries = list(dict.fromkeys(round_queries + broader + hashtags))

    return list(collected.values())


async def _safe_fetch(platform: str, search_id: uuid.UUID, fn) -> list[RawVideo]:
    await _emit(search_id, f"fetching_{platform}")
    try:
        return await fn()
    except SourceError as exc:
        await _emit(search_id, f"{platform}_error", message=str(exc))
        return []
    except Exception as exc:  # noqa: BLE001 - a source going down must not kill the search
        await _emit(search_id, f"{platform}_error", message=f"Unexpected error: {exc}")
        return []
