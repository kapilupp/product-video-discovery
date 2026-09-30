from dataclasses import dataclass


@dataclass
class RawVideo:
    platform: str
    platform_video_id: str
    video_url: str
    thumbnail_url: str | None
    caption: str | None


class SourceError(Exception):
    """Raised when a source fails after retries; the pipeline catches this
    per-source so one bad source never kills the whole search."""


class RequestBudget:
    """Hard cap on real HTTP calls to a provider, shared across an entire
    search -- including every round of the pipeline's query-widening/top-up
    loop, which calls a source function multiple times. Without sharing one
    instance across those calls, each call would get its own fresh budget
    and the effective per-search cap would multiply by the number of
    widening rounds, which matters a lot on a tight-quota free tier."""

    def __init__(self, limit: int):
        self.remaining = limit

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True
