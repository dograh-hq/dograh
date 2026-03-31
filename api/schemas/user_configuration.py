from datetime import datetime

from pydantic import BaseModel, model_validator

from api.services.configuration.registry import (
    EmbeddingsConfig,
    LLMConfig,
    RealtimeConfig,
    STTConfig,
    TTSConfig,
)


class UserConfiguration(BaseModel):
    llm: LLMConfig | None = None
    stt: STTConfig | None = None
    tts: TTSConfig | None = None
    embeddings: EmbeddingsConfig | None = None
    realtime: RealtimeConfig | None = None
    is_realtime: bool = False
    test_phone_number: str | None = None
    timezone: str | None = None
    last_validated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def strip_incomplete_realtime_when_disabled(cls, data):
        """Skip realtime validation when is_realtime is False and api_key is missing."""
        if isinstance(data, dict) and not data.get("is_realtime", False):
            realtime = data.get("realtime")
            if isinstance(realtime, dict) and not realtime.get("api_key"):
                data.pop("realtime", None)
        return data
