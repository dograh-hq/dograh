"""Organization-visible TTS cache metadata; audio is fetched separately."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TTSCacheSort = Literal["last_used", "duration", "usage"]
TTSCacheOrder = Literal["asc", "desc"]


class TTSCacheEntry(BaseModel):
    id: str
    text_preview: str
    provider: str
    model: str
    voice_id: str
    duration_seconds: float = Field(ge=0)
    hit_count: int = Field(
        ge=0, description="Synthesis requests served from this entry; excludes previews"
    )
    created_at: datetime
    last_used_at: datetime


class TTSCacheList(BaseModel):
    entries: list[TTSCacheEntry]
    total: int


class TTSCacheInvalidation(BaseModel):
    removed: int
