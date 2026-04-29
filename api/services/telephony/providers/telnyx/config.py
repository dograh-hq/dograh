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
    # Phone numbers are managed via the dedicated phone-numbers endpoints; the
    # legacy /telephony-config POST shim still accepts them inline.
    from_numbers: List[str] = Field(
        default_factory=list, description="List of Telnyx phone numbers"
    )


class TelnyxConfigurationResponse(BaseModel):
    """Response schema for Telnyx configuration with masked sensitive fields."""

    provider: Literal["telnyx"] = Field(default="telnyx")
    api_key: str  # Masked
    connection_id: str
    from_numbers: List[str]
