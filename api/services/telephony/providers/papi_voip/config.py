"""Papi Voip telephony configuration schemas."""

from typing import List, Literal

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class PapiVoipConfigurationRequest(BaseModel):
    """Request schema for Papi Voip (Papi GO Cloud) configuration."""

    provider: Literal["papi_voip"] = Field(default="papi_voip")
    base_url: str = Field(
        default="https://api.papi.api.br",
        description="Papi GO Cloud API base URL (HTTPS, no trailing slash)",
    )
    api_key: str = Field(
        ...,
        description="API key for this WhatsApp instance with Voice/VoIP enabled",
    )
    instance_id: str = Field(
        ...,
        description="WhatsApp instance id with voice/SIP enabled on Papi",
    )
    # Managed via phone-numbers endpoints; legacy save still accepts inline.
    from_numbers: List[str] = Field(
        default_factory=list,
        description="Optional display/caller numbers (E.164 without + preferred)",
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        url = (v or "").strip().rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("base_url must be a valid HTTPS URL with a hostname")
        return url


class PapiVoipConfigurationResponse(BaseModel):
    """Response schema for Papi Voip configuration with masked secrets."""

    provider: Literal["papi_voip"] = Field(default="papi_voip")
    base_url: str
    api_key: str  # Masked
    instance_id: str
    from_numbers: List[str]
