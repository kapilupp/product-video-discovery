"""
Meta Ad Library source.

Method used: the official Meta Ad Library API (Graph API `ads_archive`
endpoint), which is public for anyone with a Meta developer access token —
no login wall for the caller, but Meta rate-limits per app and requires
a search term plus `ad_type`.

Rate limits / missing data:
  - Meta returns a `paging.next` cursor; we follow it until we hit
    `min_results` or run out of pages, per query term.
  - 4xx from Meta (bad token, disabled app) raises SourceError immediately —
    no point retrying an auth failure. 5xx / 429 get retried.
  - Video ads without a `video_hd_url`/`video_sd_url` in the creative are
    skipped (Meta sometimes only returns an image creative for a "video" ad).

Accepts a list of query terms (like the Instagram source) so the pipeline's
query-widening/top-up loop can pass broader terms when the first pass comes
up short of the 20-video minimum.
"""
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.services.sources.base import RawVideo, RequestBudget, SourceError

GRAPH_URL = "https://graph.facebook.com/v20.0/ads_archive"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(httpx.HTTPStatusError),
    reraise=True,
)
async def _fetch_page(url: str, params: dict | None) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params)
        if resp.status_code in (429, 500, 502, 503):
            resp.raise_for_status()  # triggers retry
        if resp.status_code >= 400:
            raise SourceError(f"Meta Ad Library error {resp.status_code}: {resp.text[:200]}")
        return resp.json()


async def _fetch_for_query(
    query: str, remaining: int, max_pages: int, budget: RequestBudget
) -> dict[str, RawVideo]:
    collected: dict[str, RawVideo] = {}
    url = GRAPH_URL
    params = {
        "access_token": settings.meta_access_token,
        "ad_type": "ALL",
        "ad_reached_countries": f"['{settings.meta_ad_library_country}']",
        "search_terms": query,
        "fields": "id,ad_creative_link_captions,ad_creative_bodies,ad_snapshot_url,videos",
        "limit": 25,
    }

    for _ in range(max_pages):
        if len(collected) >= remaining:
            break
        if not budget.take():
            break  # hard per-search request cap hit; return whatever we have
        try:
            data = await _fetch_page(url, params)
        except (SourceError, httpx.HTTPStatusError):
            break

        for item in data.get("data", []):
            videos = item.get("videos") or []
            if not videos:
                continue  # image-only ad, not a video ad
            video_info = videos[0]
            video_url = video_info.get("video_hd_url") or video_info.get("video_sd_url")
            if not video_url:
                continue
            vid = RawVideo(
                platform="meta",
                platform_video_id=item["id"],
                video_url=video_url,
                thumbnail_url=video_info.get("video_preview_image_url"),
                caption=" ".join(item.get("ad_creative_bodies") or []) or None,
            )
            collected[vid.platform_video_id] = vid

        next_url = data.get("paging", {}).get("next")
        if not next_url:
            break
        url, params = next_url, None  # `next` is a fully-formed URL with its own query string

    return collected


async def search_meta_ad_library(
    queries: list[str],
    min_results: int = 20,
    max_pages: int = 5,
    budget: RequestBudget | None = None,
    already_tried: set[str] | None = None,
) -> list[RawVideo]:
    if not settings.meta_access_token:
        raise SourceError("META_ACCESS_TOKEN not configured")

    # `budget` and `already_tried` are shared across every round of the
    # pipeline's query-widening loop when the caller passes them in; fresh
    # ones here only cover this single call (e.g. in isolated tests).
    if budget is None:
        budget = RequestBudget(settings.meta_max_requests_per_search)
    if already_tried is None:
        already_tried = set()

    collected: dict[str, RawVideo] = {}

    for query in queries:
        normalized = query.strip().lower()
        if not normalized or normalized in already_tried:
            continue
        if len(collected) >= min_results or budget.remaining <= 0:
            break
        already_tried.add(normalized)
        try:
            page_results = await _fetch_for_query(query, min_results - len(collected), max_pages, budget)
        except SourceError:
            continue  # try the next query term instead of failing the whole source
        collected.update(page_results)

    return list(collected.values())
