"""
Tiny in-memory pub/sub so the SSE endpoint can stream pipeline progress to
the frontend. Good enough for a single-instance take-home deployment; for a
multi-worker deploy this would move to Redis pub/sub (noted in README).
"""
import asyncio
import uuid

_queues: dict[uuid.UUID, asyncio.Queue] = {}


def get_queue(search_id: uuid.UUID) -> asyncio.Queue:
    if search_id not in _queues:
        _queues[search_id] = asyncio.Queue()
    return _queues[search_id]


async def publish(search_id: uuid.UUID, event: dict) -> None:
    await get_queue(search_id).put(event)


async def close(search_id: uuid.UUID) -> None:
    await get_queue(search_id).put(None)  # sentinel: stream is done


def cleanup(search_id: uuid.UUID) -> None:
    _queues.pop(search_id, None)
