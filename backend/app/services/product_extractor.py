"""
Opens a product page URL and pulls out title, description and main image.

Security note: before fetching, we block internal/private addresses (SSRF
guard) since the URL comes straight from user input. Redirects are followed
manually, one hop at a time, re-checking each target URL -- otherwise a
public URL that 302s to an internal address would slip past the initial
check (httpx's built-in follow_redirects only validates the first hop).

Caching: the assignment requires that "the same product link is not
re-processed every time". Results are cached in-process, keyed by the
normalized URL, for CACHE_TTL_SECONDS. A single-instance deployment is all
this take-home targets; a multi-worker deployment would move this to Redis
(same trade-off as services/progress_bus.py).
"""
import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
CACHE_TTL_SECONDS = 60 * 60  # 1 hour

_cache: dict[str, tuple[float, dict]] = {}


class UnsafeUrlError(Exception):
    pass


class ExtractionError(Exception):
    pass


def assert_safe_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("Only http/https URLs are allowed")
    if not parsed.hostname:
        raise UnsafeUrlError("URL has no host")

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve host: {parsed.hostname}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise UnsafeUrlError(f"Blocked unsafe/internal address: {ip}")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers={"User-Agent": "Mozilla/5.0 (ProductVideoDiscoveryBot)"})


async def _fetch_following_redirects(url: str) -> httpx.Response:
    current_url = url
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS):
            assert_safe_url(current_url)
            resp = await _get(client, current_url)
            if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                current_url = str(resp.next_request.url) if resp.next_request else resp.headers["location"]
                continue
            resp.raise_for_status()
            return resp
    raise ExtractionError(f"Too many redirects (>{MAX_REDIRECTS}) resolving {url}")


def _normalize(url: str) -> str:
    return url.strip().rstrip("/")


async def extract_product(url: str) -> dict:
    key = _normalize(url)
    cached = _cache.get(key)
    if cached and (time.monotonic() - cached[0]) < CACHE_TTL_SECONDS:
        logger.info("product_extractor cache hit for %s", key)
        return cached[1]

    assert_safe_url(url)  # fail fast on the obviously-unsafe case before any network call
    resp = await _fetch_following_redirects(url)

    soup = BeautifulSoup(resp.text, "lxml")

    title = _first_meta(soup, ["og:title", "twitter:title"]) or (soup.title.string if soup.title else None)
    description = _first_meta(soup, ["og:description", "description", "twitter:description"])
    image = _first_meta(soup, ["og:image", "twitter:image"])

    # Shopify/common e-commerce fallback: JSON-LD Product schema
    if not (title and image):
        ld = _find_ld_json_product(soup)
        if ld:
            title = title or ld.get("name")
            description = description or ld.get("description")
            image = image or _first_str(ld.get("image"))

    if not title and not image:
        raise ExtractionError(
            f"Could not find a product title or image on {url} "
            "(no og:title/og:image meta tags or JSON-LD Product schema found)"
        )

    result = {
        "title": (title or "").strip() or None,
        "description": (description or "").strip() if description else None,
        "image_url": image,
    }
    _cache[key] = (time.monotonic(), result)
    return result


def _first_meta(soup: BeautifulSoup, properties: list[str]) -> str | None:
    for prop in properties:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            return tag["content"]
    return None


def _find_ld_json_product(soup: BeautifulSoup) -> dict | None:
    import json

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "{}")
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    return None


def _first_str(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value
