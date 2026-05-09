"""Telnyx telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TelnyxConfigurationRequest(BaseModel):
    """Request schema for Telnyx configuration."""

    provider: Literal["telnyx"] = Field(default="telnyx")
    api_key: str = Field(..., description="Telnyx API Key")
    connection_id: Optional[str] = Field(
        default=None,
        description=(
            "Telnyx Call Control Application ID (connection_id). If omitted, "
            "a Call Control Application is auto-created on save and its id is "
            "stored on the configuration."
        ),
    )
    webhook_public_key: Optional[str] = Field(
        default=None,
        description=(
            "Webhook public key from Mission Control Portal → Keys & "
            "Credentials → Public Key. Used to verify Telnyx webhook "
            "signatures."
        ),
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
    connection_id: Optional[str] = None
    webhook_public_key: Optional[str] = None
    from_numbers: List[str]
