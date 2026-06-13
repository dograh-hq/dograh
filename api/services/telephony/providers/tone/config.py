"""Tone telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ToneConfigurationRequest(BaseModel):
    """Request schema for Tone configuration."""

    provider: Literal["tone"] = Field(default="tone")
    api_key: str = Field(..., description="Tone API key (Bearer token)")
    from_numbers: List[str] = Field(
        default_factory=list,
        description="E.164-formatted Tone phone numbers, e.g. ['+917314624707']",
    )


class ToneConfigurationResponse(BaseModel):
    """Response schema for Tone configuration with masked sensitive fields."""

    provider: Literal["tone"] = Field(default="tone")
    api_key: str  # Masked on return
    from_numbers: List[str]
