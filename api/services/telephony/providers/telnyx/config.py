"""Telnyx telephony configuration schemas."""

from typing import Literal, Optional

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
