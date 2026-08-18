"""Twilio telephony configuration schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class TwilioConfigurationRequest(BaseModel):
    """Request schema for Twilio configuration."""

    provider: Literal["twilio"] = Field(default="twilio")
    account_sid: str = Field(..., description="Twilio Account SID")
    auth_token: str = Field(..., description="Twilio Auth Token")
    amd_enabled: bool = Field(
        default=False,
        description=(
            "Detect whether outbound calls are answered by a person or machine. "
            "Twilio may bill AMD as an additional per-call feature."
        ),
    )
