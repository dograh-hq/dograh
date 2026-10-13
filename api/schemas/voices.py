from pydantic import BaseModel, Field


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    description: str | None = None
    accent: str | None = None
    gender: str | None = None
    language: str | None = None
    preview_url: str | None = None


class VoiceFacets(BaseModel):
    """Distinct selector values across a provider's full voice catalog."""

    genders: list[str] = Field(default_factory=list)
    accents: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class VoicesResponse(BaseModel):
    provider: str
    voices: list[VoiceInfo]
    facets: VoiceFacets | None = None
