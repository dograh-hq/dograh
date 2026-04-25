"""Telnyx telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class TelnyxConfigurationRequest(BaseModel):
    """Request schema for Telnyx configuration."""

    provider: Literal["telnyx"] = Field(default="telnyx")
    api_key: str = Field(..., description="Telnyx API Key")
    connection_id: str = Field(
        ..., description="Telnyx Call Control Application ID (connection_id)"
    )
    from_numbers: List[str] = Field(
        ..., min_length=1, description="List of Telnyx phone numbers (E.164 format)"
    )


class TelnyxConfigurationResponse(BaseModel):
    """Response schema for Telnyx configuration with masked sensitive fields."""

    provider: Literal["telnyx"] = Field(default="telnyx")
    api_key: str  # Masked
    connection_id: str
    from_numbers: List[str]
