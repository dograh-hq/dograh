"""Plivo telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class PlivoConfigurationRequest(BaseModel):
    """Request schema for Plivo configuration."""

    provider: Literal["plivo"] = Field(default="plivo")
    auth_id: str = Field(..., description="Plivo Auth ID")
    auth_token: str = Field(..., description="Plivo Auth Token")
    from_numbers: List[str] = Field(
        ..., min_length=1, description="List of Plivo phone numbers"
    )


class PlivoConfigurationResponse(BaseModel):
    """Response schema for Plivo configuration with masked sensitive fields."""

    provider: Literal["plivo"] = Field(default="plivo")
    auth_id: str  # Masked
    auth_token: str  # Masked
    from_numbers: List[str]
