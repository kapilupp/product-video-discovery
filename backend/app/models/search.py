import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class SearchStatus(str, enum.Enum):
    pending = "pending"
    extracting_product = "extracting_product"
    analyzing_image = "analyzing_image"
    fetching_instagram = "fetching_instagram"
    fetching_meta = "fetching_meta"
    fetching_tiktok = "fetching_tiktok"
    scoring = "scoring"
    done = "done"
    failed = "failed"


class Search(Base):
    __tablename__ = "searches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    query_text: Mapped[str | None] = mapped_column(String, nullable=True)
    product_url: Mapped[str | None] = mapped_column(String, nullable=True)

    product_title: Mapped[str | None] = mapped_column(String, nullable=True)
    product_description: Mapped[str | None] = mapped_column(String, nullable=True)
    product_image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    product_attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[SearchStatus] = mapped_column(
        Enum(SearchStatus, name="search_status"), default=SearchStatus.pending
    )
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    instagram_count: Mapped[int] = mapped_column(default=0)
    meta_count: Mapped[int] = mapped_column(default=0)
    tiktok_count: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    videos: Mapped[list["Video"]] = relationship(back_populates="search", cascade="all, delete-orphan")
