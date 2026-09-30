"""
Instagram Reels source.

Method used (documented per assignment requirement): Instagram has no public
API for "search reels by hashtag/keyword" for third-party apps, so we go
through a RapidAPI provider -- "Instagram Scraper Stable API"
(instagram-scraper-stable-api.p.rapidapi.com). Its "Posts & Reels V2 (with
pagination)" endpoint (`GET /search_hashtag.php?hashtag=<tag>`) returns a
hashtag's recent posts, mixing photos and videos; verified against the live
API on 2026-09-30 (see the response shape below).

Real response shape (as returned by the provider):
    {
      "name": "tshirt",
      "posts": {
        "count": 56804842,
        "edges": [
          {"node": {
            "id": "3997603149913098079",
            "shortcode": "Dd6VuJ3jYdf",
            "is_video": false,
            "display_url": "https://...jpg",
            "thumbnail_src": "https://...jpg",
            "edge_media_to_caption": {"edges": [{"node": {"text": "..."}}]},
            ...
          }}
        ]
      }
    }
A hashtag's feed is mostly photos, so results are filtered to
`is_video: true` -- this endpoint doesn't expose a stable "video file URL"
field for every video node, so the video link falls back to the public
`instagram.com/reel/<shortcode>/` permalink (which always works and is what
a user would click through to anyway).

Rate limits / blocks / missing data:
  - `tenacity` retries TRANSIENT failures only (429/5xx, network errors) up
    to 3x with backoff. A permanent failure (404 wrong endpoint path, 401/403
    bad key) is NOT retried -- retrying a broken endpoint just burns quota
    for no benefit, which matters a lot on a free-tier plan with a tight
    monthly request cap.
  - A permanent failure aborts the whole search_instagram_reels call
    immediately (instead of silently trying every remaining term), so one
    bad endpoint costs exactly one request, not one per term.
  - `INSTAGRAM_MAX_REQUESTS_PER_SEARCH` is a hard budget: even once
    query-widening logic kicks in, a single search can never make more than
    this many real HTTP calls to the provider, protecting a tight monthly
    quota from any widening-logic bug. This endpoint has no page parameter
    (only an optional `pagination_token` this integration doesn't use yet),
    so budget == number of distinct hashtag terms tried.
  - If fewer than `min_results` come back, the caller (pipeline.py) widens
    the query using extra hashtags from the image brain, within that same
    request budget.
"""
import re

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.services.sources.base import RawVideo, RequestBudget, SourceError


class TransientProviderError(SourceError):
    """Worth retrying: rate-limited or a momentary provider/network hiccup."""


class PermanentProviderError(SourceError):
    """Not worth retrying: wrong endpoint path, bad/expired key, etc. Every
    other term would fail identically, so callers should stop immediately
    instead of burning through the rest of the request budget."""


def _to_hashtag(term: str) -> str:
    """Instagram hashtags have no spaces/punctuation -- "blue jeans" -> "bluejeans"."""
    return re.sub(r"[^0-9a-zA-Z]", "", term).lower()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, TransientProviderError)),
    reraise=True,
)
async def _fetch_hashtag_posts(hashtag: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"https://{settings.instagram_api_host}/search_hashtag.php",
            headers={
                "X-RapidAPI-Key": settings.instagram_api_key,
                "X-RapidAPI-Host": settings.instagram_api_host,
            },
            params={"hashtag": hashtag},
        )

    if resp.status_code == 429 or 500 <= resp.status_code < 600:
        raise TransientProviderError(f"Instagram provider returned {resp.status_code}, retrying")
    if resp.status_code >= 400:
        raise PermanentProviderError(
            f"Instagram provider returned {resp.status_code} for {resp.request.url} -- "
            "check INSTAGRAM_API_HOST/the endpoint path and INSTAGRAM_API_KEY, this will not "
            "resolve itself by retrying"
        )

    data = resp.json()
    edges = (data.get("posts") or {}).get("edges") or []
    return [edge["node"] for edge in edges if "node" in edge]


def _extract_caption(node: dict) -> str | None:
    edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
    if edges and "node" in edges[0]:
        return edges[0]["node"].get("text")
    return None


async def search_instagram_reels(
    queries: list[str],
    hashtags: list[str],
    min_results: int = 20,
    max_pages: int = 5,
    budget: RequestBudget | None = None,
    already_tried: set[str] | None = None,
) -> list[RawVideo]:
    if not settings.instagram_api_key:
        raise SourceError("INSTAGRAM_API_KEY not configured")

    # `budget` and `already_tried` are shared across every round of the
    # pipeline's query-widening loop when the caller passes them in; fresh
    # ones here only cover this single call (e.g. in isolated tests). Without
    # `already_tried`, a widening round that lands on the same hashtag as a
    # previous round would spend another request for a result we already have.
    if budget is None:
        budget = RequestBudget(settings.instagram_max_requests_per_search)
    if already_tried is None:
        already_tried = set()

    collected: dict[str, RawVideo] = {}
    # image-brain hashtags already come with a leading "#" (e.g. "#tshirt");
    # queries are free-text phrases (e.g. "blue jeans") that need collapsing.
    terms = list(dict.fromkeys(_to_hashtag(t) for t in (queries + hashtags) if t))

    for hashtag in terms:
        if not hashtag or hashtag in already_tried:
            continue
        if len(collected) >= min_results:
            return list(collected.values())
        if not budget.take():
            return list(collected.values())  # hard budget hit; return whatever we have

        already_tried.add(hashtag)
        try:
            nodes = await _fetch_hashtag_posts(hashtag)
        except PermanentProviderError:
            raise  # every other term would fail the same way -- stop spending budget
        except (TransientProviderError, httpx.HTTPStatusError, httpx.TransportError):
            continue  # this term is having a bad time; try the next one instead

        for node in nodes:
            if not node.get("is_video"):
                continue  # this endpoint mixes in photo posts; we only want video (Reels)

            video_id = node.get("id") or node.get("shortcode")
            if not video_id:
                continue

            shortcode = node.get("shortcode")
            vid = RawVideo(
                platform="instagram",
                platform_video_id=str(video_id),
                video_url=f"https://www.instagram.com/reel/{shortcode}/" if shortcode else "",
                thumbnail_url=node.get("thumbnail_src") or node.get("display_url"),
                caption=_extract_caption(node),
            )
            if vid.video_url:
                collected[vid.platform_video_id] = vid

    return list(collected.values())
