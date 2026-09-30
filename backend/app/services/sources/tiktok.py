"""
Optional bonus source. Disabled unless TIKTOK_ENABLED=true, and it runs in
its own try/except in the pipeline so it can never block Instagram or Meta.
"""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.services.sources.base import RawVideo, SourceError


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=6), reraise=True)
async def search_tiktok(query: str, min_results: int = 0) -> list[RawVideo]:
    if not settings.tiktok_enabled:
        return []
    if not settings.tiktok_api_key:
        raise SourceError("TIKTOK_API_KEY not configured")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://tiktok-scraper-provider.example.com/search",
            headers={"Authorization": f"Bearer {settings.tiktok_api_key}"},
            params={"query": query},
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

    return [
        RawVideo(
            platform="tiktok",
            platform_video_id=item["id"],
            video_url=item["url"],
            thumbnail_url=item.get("thumbnail_url"),
            caption=item.get("caption"),
        )
        for item in items
    ]
