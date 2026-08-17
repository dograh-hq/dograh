"""Papi Voip telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class PapiVoipConfigurationRequest(BaseModel):
    """Request schema for Papi Voip (Papi GO Cloud) configuration."""

    provider: Literal["papi_voip"] = Field(default="papi_voip")
    base_url: str = Field(
        default="https://api.papi.api.br",
        description="Papi GO Cloud API base URL (HTTPS, no trailing slash)",
    )
    api_key: str = Field(
        ...,
        description="Papi API key (global API_KEY or instance key)",
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


class PapiVoipConfigurationResponse(BaseModel):
    """Response schema for Papi Voip configuration with masked secrets."""

    provider: Literal["papi_voip"] = Field(default="papi_voip")
    base_url: str
    api_key: str  # Masked
    instance_id: str
    from_numbers: List[str]
