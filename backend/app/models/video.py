import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Video(Base):
    """
    One row per video ever returned to the user, across all searches.

    `dedup_key` is the platform video ID when we have one, otherwise a
    perceptual hash of the thumbnail + a hash of the media URL. It is what
    the uniqueness/de-dup logic checks against past searches.
    """

    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("search_id", "dedup_key", name="uq_video_per_search"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    search_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("searches.id", ondelete="CASCADE"))

    platform: Mapped[str] = mapped_column(String)  # "instagram" | "meta" | "tiktok"
    dedup_key: Mapped[str] = mapped_column(String, index=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    video_url: Mapped[str] = mapped_column(String)
    thumbnail_url: Mapped[str | None] = mapped_column(String, nullable=True)
    caption: Mapped[str | None] = mapped_column(String, nullable=True)

    match_score: Mapped[int] = mapped_column(Integer, default=0)
    match_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    seen_before: Mapped[bool] = mapped_column(default=False)  # returned in an earlier search
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    search: Mapped["Search"] = relationship(back_populates="videos")


class SeenVideo(Base):
    """
    Global registry of every dedup_key ever shown to the user, independent of
    which search first returned it. Used to filter out repeats on new searches.
    """

    __tablename__ = "seen_videos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String)
    dedup_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    first_seen_search_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
