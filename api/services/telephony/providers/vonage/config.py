"""Vonage telephony configuration schemas."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class VonageConfigurationRequest(BaseModel):
    """Request schema for Vonage configuration."""

    provider: Literal["vonage"] = Field(default="vonage")
    api_key: str = Field(..., description="Vonage API Key")
    api_secret: str = Field(..., description="Vonage API Secret")
    application_id: str = Field(..., description="Vonage Application ID")
    private_key: str = Field(..., description="Private key for JWT generation")
    signature_secret: Optional[str] = Field(
        None,
        description="Vonage signature secret used to verify signed webhooks",
    )
