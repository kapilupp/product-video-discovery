"""
Instagram Reels source -- browser automation variant.

Method used: headless Chromium (Playwright) opens Instagram's public
hashtag explore page (`instagram.com/explore/tags/<hashtag>/`) and reads the
same GraphQL/JSON payload the page itself loads client-side, by intercepting
the network response rather than scraping rendered HTML (the DOM alone is
fragile -- Instagram renders almost everything via client-side JS after
initial load, and class names/structure change often; the underlying JSON
response is comparatively stable).

This is the assignment's explicitly-allowed "browser automation" method,
used here as a zero-quota alternative to a paid/rate-limited third-party API
(see INSTAGRAM_PROVIDER=browser vs. INSTAGRAM_PROVIDER=rapidapi in
instagram.py). Trade-off: no request quota to manage, but higher risk of
Instagram showing a login wall, a CAPTCHA/challenge page, or throttling
based on IP/traffic patterns -- all of which we detect and surface as a
clear SourceError rather than crash or hang.

Rate limits / blocks / missing data:
  - A login wall or challenge page is detected (URL redirects to
    accounts/login or /challenge/) and raised as a SourceError immediately
    -- retrying the same request would hit the same wall.
  - A page load timeout (slow network, Instagram serving an unusual
    layout) also raises SourceError rather than hanging the whole search.
  - Only posts marked as video in the response are kept, mirroring the
    RapidAPI variant's `is_video` filter.
  - No pagination: a single hashtag page load returns Instagram's default
    "top posts" batch (typically ~9-33 items depending on layout). Widening
    across more hashtags (via pipeline.py's query-widening loop) is the
    primary way to reach the 20-video minimum with this method.
"""
import asyncio
import json
import re

from app.services.sources.base import RawVideo, SourceError

HASHTAG_URL = "https://www.instagram.com/explore/tags/{hashtag}/"
PAGE_TIMEOUT_MS = 20_000
_playwright = None
_browser = None
_browser_lock = asyncio.Lock()


def _to_hashtag(term: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]", "", term).lower()


async def _get_browser():
    """Reuses one headless browser instance across calls in this process
    instead of launching a fresh one per request (launching Chromium is
    the slow part of every call by far)."""
    global _playwright, _browser
    async with _browser_lock:
        if _browser is None:
            from playwright.async_api import async_playwright

            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=True)
        return _browser


async def close_browser() -> None:
    """Call on app shutdown to release the headless browser cleanly."""
    global _playwright, _browser
    async with _browser_lock:
        if _browser is not None:
            await _browser.close()
            _browser = None
        if _playwright is not None:
            await _playwright.stop()
            _playwright = None


async def _fetch_hashtag_nodes(hashtag: str) -> list[dict]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    browser = await _get_browser()
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 1000},
    )
    page = await context.new_page()

    captured: dict = {}
    auth_error: dict = {}

    async def _capture_response(response):
        url = response.url
        if "graphql" not in url and "/api/v1/" not in url:
            return
        try:
            data = await response.json()
        except Exception:
            return
        if not isinstance(data, dict):
            return
        # Confirmed by manual investigation (2026-09-30): Instagram's hashtag
        # media GraphQL query returns this specific error for logged-out
        # sessions -- it is a server-side authorization block, not a
        # renderable "please log in" page, so it must be caught here rather
        # than by looking for login-page UI/URL patterns.
        for err in data.get("errors") or []:
            if err.get("code") == 1675002 or "logged out" in (err.get("message") or "").lower():
                auth_error["message"] = err.get("message")
        if _looks_like_hashtag_payload(data):
            captured["data"] = data

    page.on("response", lambda r: asyncio.ensure_future(_capture_response(r)))

    try:
        try:
            await page.goto(
                HASHTAG_URL.format(hashtag=hashtag), timeout=PAGE_TIMEOUT_MS, wait_until="networkidle"
            )
        except PlaywrightTimeoutError as exc:
            raise SourceError(f"Timed out loading Instagram hashtag page for #{hashtag}") from exc

        final_url = page.url
        if "accounts/login" in final_url or "/challenge/" in final_url:
            raise SourceError(
                f"Instagram showed a login wall/challenge for #{hashtag} -- "
                "anonymous browser access is currently blocked for this hashtag"
            )

        # Give any late XHR/GraphQL responses a moment to land after networkidle.
        await page.wait_for_timeout(1500)

        if auth_error:
            raise SourceError(
                f"Instagram's hashtag media API rejected the logged-out request for #{hashtag} "
                f"({auth_error['message']!r}) -- the page itself loads, but Instagram now requires "
                "an authenticated session server-side to read a hashtag's actual posts; a real "
                "login session (with its own ToS/ban risk) would be required to get past this"
            )

        if "data" in captured:
            return _extract_nodes(captured["data"])

        # Fallback: look for an embedded JSON blob in a <script> tag, in case
        # this layout serves data inline instead of via a captured XHR.
        html = await page.content()
        inline = _extract_inline_json(html)
        return _extract_nodes(inline) if inline else []
    finally:
        await context.close()


def _looks_like_hashtag_payload(data: dict) -> bool:
    text = json.dumps(data)[:2000]
    return "edge_hashtag_to_media" in text or "hashtag" in text.lower() and "edges" in text


def _extract_inline_json(html: str) -> dict | None:
    match = re.search(r'<script type="application/json"[^>]*>(\{.*?"hashtag".*?\})</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _extract_nodes(data: dict) -> list[dict]:
    """Instagram's hashtag JSON is deeply nested and the exact path has
    shifted over the years; walk the structure looking for the first
    `edge_hashtag_to_media`-style edges list rather than hardcoding one path."""
    nodes: list[dict] = []

    def _walk(obj):
        if isinstance(obj, dict):
            if "edges" in obj and isinstance(obj["edges"], list):
                for edge in obj["edges"]:
                    node = edge.get("node") if isinstance(edge, dict) else None
                    if isinstance(node, dict) and ("shortcode" in node or "code" in node):
                        nodes.append(node)
            for value in obj.values():
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    # De-dupe by shortcode/code in case the same node appeared via multiple paths.
    seen_codes = set()
    unique = []
    for node in nodes:
        code = node.get("shortcode") or node.get("code")
        if code and code not in seen_codes:
            seen_codes.add(code)
            unique.append(node)
    return unique


def _extract_caption(node: dict) -> str | None:
    edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
    if edges and "node" in edges[0]:
        return edges[0]["node"].get("text")
    return node.get("caption")


async def search_instagram_reels_via_browser(
    queries: list[str], hashtags: list[str], min_results: int = 20, max_pages: int = 5
) -> list[RawVideo]:
    collected: dict[str, RawVideo] = {}
    terms = list(dict.fromkeys(_to_hashtag(t) for t in (queries + hashtags) if t))

    for hashtag in terms:
        if not hashtag:
            continue
        if len(collected) >= min_results:
            break

        try:
            nodes = await _fetch_hashtag_nodes(hashtag)
        except SourceError:
            raise  # a login wall on one hashtag means the same wall on every hashtag; stop here
        except Exception as exc:  # noqa: BLE001 - browser automation has many failure modes
            raise SourceError(f"Browser automation failed for #{hashtag}: {exc}") from exc

        for node in nodes:
            is_video = node.get("is_video") or node.get("media_type") == 2
            if not is_video:
                continue

            shortcode = node.get("shortcode") or node.get("code")
            video_id = node.get("id") or shortcode
            if not video_id or not shortcode:
                continue

            vid = RawVideo(
                platform="instagram",
                platform_video_id=str(video_id),
                video_url=f"https://www.instagram.com/reel/{shortcode}/",
                thumbnail_url=node.get("thumbnail_src") or node.get("display_url"),
                caption=_extract_caption(node),
            )
            collected[vid.platform_video_id] = vid

    return list(collected.values())
