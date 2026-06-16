"""Twilio telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class TwilioConfigurationRequest(BaseModel):
    """Request schema for Twilio configuration."""

    provider: Literal["twilio"] = Field(default="twilio")
    account_sid: str = Field(..., description="Twilio Account SID")
    auth_token: str = Field(..., description="Twilio Auth Token")
    # Phone numbers are managed via the dedicated phone-numbers endpoints; the
    # legacy /telephony-config POST shim still accepts them inline.
    from_numbers: List[str] = Field(
        default_factory=list, description="List of Twilio phone numbers"
    )
    amd_enabled: bool = Field(
        default=False,
        description=(
            "Enable Twilio Answering Machine Detection (AMD) on outbound calls. "
            "When enabled, Twilio fires a status callback with AnsweredBy=human "
            "or AnsweredBy=machine before connecting the call."
        ),
    )


class TwilioConfigurationResponse(BaseModel):
    """Response schema for Twilio configuration with masked sensitive fields."""

    provider: Literal["twilio"] = Field(default="twilio")
    account_sid: str  # Masked (e.g., "****************def0")
    auth_token: str  # Masked (e.g., "****************abc1")
    from_numbers: List[str]
    amd_enabled: bool = False
