from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.search import Search
from app.schemas.search import SearchHistoryItem

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=list[SearchHistoryItem])
async def list_history(db: AsyncSession = Depends(get_db), limit: int = 50):
    result = await db.execute(select(Search).order_by(Search.created_at.desc()).limit(limit))
    return result.scalars().all()
