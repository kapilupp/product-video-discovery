import asyncio
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import StreamingResponse

from app.core.db import SessionLocal, get_db
from app.models.search import Search, SearchStatus
from app.models.video import Video
from app.schemas.search import SearchCreateRequest, SearchOut
from app.services import progress_bus
from app.services.pipeline import run_search_pipeline

TERMINAL_STATUSES = (SearchStatus.done, SearchStatus.failed)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchOut, status_code=202)
async def create_search(
    payload: SearchCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if not payload.query_text and not payload.product_url:
        raise HTTPException(400, "Provide either query_text or product_url")

    search = Search(
        query_text=payload.query_text,
        product_url=payload.product_url,
        product_image_url=payload.image_base64,  # uploaded photo takes priority; see pipeline.py
    )
    db.add(search)
    await db.commit()

    # Re-fetch with `videos` eagerly loaded: assigning to `.videos` directly
    # on the now-persistent object would make SQLAlchemy load the *previous*
    # collection first (to compute cascade/backref diffs), which triggers a
    # synchronous DB call outside of an awaitable context during response
    # serialization and raises MissingGreenlet. A fresh selectinload avoids
    # that entirely, at the cost of one extra (cheap, indexed) query.
    result = await db.execute(
        select(Search).where(Search.id == search.id).options(selectinload(Search.videos))
    )
    search = result.scalar_one()

    background_tasks.add_task(run_search_pipeline, search.id)

    return search


@router.get("/{search_id}", response_model=SearchOut)
async def get_search(search_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Search).where(Search.id == search_id).options(selectinload(Search.videos))
    )
    search = result.scalar_one_or_none()
    if not search:
        raise HTTPException(404, "Search not found")
    return search


def _terminal_payload(search: Search) -> dict:
    payload = {"stage": search.status.value}
    if search.status == SearchStatus.failed:
        payload["message"] = search.error_message
    else:
        payload["instagram_count"] = search.instagram_count
        payload["meta_count"] = search.meta_count
        payload["tiktok_count"] = search.tiktok_count
    return payload


async def _get_status(search_id: uuid.UUID) -> Search | None:
    async with SessionLocal() as db:
        return await db.get(Search, search_id)


@router.get("/{search_id}/stream")
async def stream_progress(search_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    search = await db.get(Search, search_id)
    if not search:
        raise HTTPException(404, "Search not found")

    async def event_generator():
        # Fast path: the pipeline already finished (or this is a re-opened
        # history item) before the client subscribed -- its progress queue
        # may already be cleaned up, so poll-and-reply instead of blocking
        # on a queue that will never receive anything.
        if search.status in TERMINAL_STATUSES:
            yield f"data: {json.dumps(_terminal_payload(search))}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        queue = progress_bus.get_queue(search_id)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                # Guards the narrower race where the pipeline finishes (and
                # cleans up its queue) in between our check above and the
                # `get_queue` call just before this loop.
                refreshed = await _get_status(search_id)
                if refreshed and refreshed.status in TERMINAL_STATUSES:
                    yield f"data: {json.dumps(_terminal_payload(refreshed))}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    return
                continue

            if event is None:
                yield "event: done\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
