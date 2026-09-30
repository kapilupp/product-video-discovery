import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SearchCreateRequest(BaseModel):
    query_text: str | None = Field(default=None, description="Product name or keyword")
    product_url: str | None = Field(default=None, description="Product page URL")
    image_base64: str | None = Field(default=None, description="Optional uploaded product image")
    include_seen: bool = Field(default=False, description="'Show previously seen' toggle")


class VideoOut(BaseModel):
    id: uuid.UUID
    platform: str
    video_url: str
    thumbnail_url: str | None
    caption: str | None
    match_score: int
    match_reason: str | None
    seen_before: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SearchOut(BaseModel):
    id: uuid.UUID
    query_text: str | None
    product_url: str | None
    product_title: str | None
    product_image_url: str | None
    product_attributes: dict | None
    status: str
    error_message: str | None
    instagram_count: int
    meta_count: int
    tiktok_count: int
    videos: list[VideoOut] = []

    class Config:
        from_attributes = True


class SearchHistoryItem(BaseModel):
    id: uuid.UUID
    query_text: str | None
    product_url: str | None
    product_title: str | None
    status: str
    instagram_count: int
    meta_count: int
    tiktok_count: int

    class Config:
        from_attributes = True
