"""
Uniqueness / de-duplication.

Strategy (documented in README too):
  1. Within-batch dedup: reposts/re-uploads of the same creative within one
     result set (same ad running under several IDs) are collapsed to a
     single entry, by exact ID and by perceptual-hash near-match.
  2. Cross-search "seen before" tracking: every video ID/hash we've ever
     shown is recorded in `seen_videos`. A video matching that history is
     NOT dropped outright -- it's kept but flagged `seen_before=True`, so it
     doesn't count towards the 20-per-source minimum and is hidden by
     default, but the "Show previously seen" toggle can still surface it.
"""
from __future__ import annotations

import hashlib
import io

import httpx
import imagehash
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.video import SeenVideo
from app.services.sources.base import RawVideo

HAMMING_THRESHOLD = 6  # pHash bits that differ; <=6 is "same image, re-encoded"

Candidate = tuple[RawVideo, str, str | None]
AnnotatedCandidate = tuple[RawVideo, str, str | None, bool]  # + seen_before


def dedup_key_for(video: RawVideo) -> str:
    if video.platform_video_id:
        return f"{video.platform}:{video.platform_video_id}"
    # fallback: hash of the media URL, for sources with no stable ID
    return f"{video.platform}:{hashlib.sha256(video.video_url.encode()).hexdigest()}"


async def perceptual_hash(thumbnail_url: str | None) -> str | None:
    if not thumbnail_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(thumbnail_url)
            resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return None  # missing/broken thumbnail shouldn't crash the whole search


def _is_near_duplicate(hash_a: str, hash_b: str) -> bool:
    # imagehash's `-` returns a numpy integer and the comparison a numpy
    # bool_, not a native bool -- wrap it so callers get a real bool.
    return bool(imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b) <= HAMMING_THRESHOLD)


def dedup_within_batch(candidates: list[Candidate]) -> list[Candidate]:
    """Collapse exact/near-duplicates inside a single result set, keep the first occurrence."""
    kept: list[Candidate] = []
    kept_keys: set[str] = set()
    kept_hashes: list[str] = []

    for video, key, phash in candidates:
        if key in kept_keys:
            continue
        if phash and any(_is_near_duplicate(phash, h) for h in kept_hashes):
            continue

        kept_keys.add(key)
        if phash:
            kept_hashes.append(phash)
        kept.append((video, key, phash))

    return kept


async def estimate_new_count(db: AsyncSession, candidates: list[Candidate]) -> int:
    """Cheap (exact-key-only) estimate of how many candidates are NOT already seen.
    Used by the pipeline's query-widening loop to decide whether to fetch more."""
    if not candidates:
        return 0
    keys = [c[1] for c in candidates]
    result = await db.execute(select(SeenVideo.dedup_key).where(SeenVideo.dedup_key.in_(keys)))
    seen_keys = {row[0] for row in result.all()}
    return sum(1 for _, key, _ in candidates if key not in seen_keys)


async def annotate_seen(db: AsyncSession, candidates: list[Candidate]) -> list[AnnotatedCandidate]:
    """Marks each candidate seen_before True/False against global history. Never drops anything --
    within-batch de-duplication must already have been applied via `dedup_within_batch`."""
    if not candidates:
        return []

    keys = [c[1] for c in candidates]
    result = await db.execute(select(SeenVideo.dedup_key).where(SeenVideo.dedup_key.in_(keys)))
    seen_keys = {row[0] for row in result.all()}

    result = await db.execute(select(SeenVideo.perceptual_hash).where(SeenVideo.perceptual_hash.isnot(None)))
    seen_hashes = [h for (h,) in result.all()]

    annotated: list[AnnotatedCandidate] = []
    for video, key, phash in candidates:
        seen_before = key in seen_keys
        if not seen_before and phash:
            seen_before = any(_is_near_duplicate(phash, h) for h in seen_hashes)
        annotated.append((video, key, phash, seen_before))

    return annotated


async def mark_new_as_seen(db: AsyncSession, search_id, annotated: list[AnnotatedCandidate]) -> None:
    for video, key, phash, seen_before in annotated:
        if seen_before:
            continue  # already in seen_videos, nothing to insert
        db.add(
            SeenVideo(
                platform=video.platform,
                dedup_key=key,
                perceptual_hash=phash,
                first_seen_search_id=search_id,
            )
        )
    await db.commit()
