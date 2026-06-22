"""Tone telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


def _mask(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


class ToneConfigurationRequest(BaseModel):
    """Request schema for Tone configuration."""

    provider: Literal["tone"] = Field(default="tone")
    api_key: str = Field(..., description="Tone API key (Bearer token)")
    from_numbers: List[str] = Field(
        default_factory=list,
        description="E.164-formatted Tone phone numbers, e.g. ['+917314624707']",
    )
    webhook_secret: Optional[str] = Field(
        default=None,
        description="Optional shared secret sent in X-Tone-Webhook-Secret header for callback authentication",
    )


class ToneConfigurationResponse(BaseModel):
    """Response schema for Tone configuration with masked sensitive fields."""

    provider: Literal["tone"] = Field(default="tone")
    api_key: str
    from_numbers: List[str]
    webhook_secret: Optional[str] = None

    @model_validator(mode="after")
    def mask_sensitive_fields(self) -> "ToneConfigurationResponse":
        self.api_key = _mask(self.api_key) or "****"
        self.webhook_secret = _mask(self.webhook_secret)
        return self
