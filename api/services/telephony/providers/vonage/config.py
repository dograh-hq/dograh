"""Vonage telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class VonageConfigurationRequest(BaseModel):
    """Request schema for Vonage configuration."""

    provider: Literal["vonage"] = Field(default="vonage")
    api_key: Optional[str] = Field(None, description="Vonage API Key")
    api_secret: Optional[str] = Field(None, description="Vonage API Secret")
    application_id: str = Field(..., description="Vonage Application ID")
    private_key: str = Field(..., description="Private key for JWT generation")
    from_numbers: List[str] = Field(
        ..., min_length=1, description="List of Vonage phone numbers (without + prefix)"
    )


class VonageConfigurationResponse(BaseModel):
    """Response schema for Vonage configuration with masked sensitive fields."""

    provider: Literal["vonage"] = Field(default="vonage")
    application_id: str  # Not sensitive, can show full
    api_key: Optional[str]  # Masked if present
    api_secret: Optional[str]  # Masked if present
    private_key: str  # Masked
    from_numbers: List[str]
