from typing import List

from pydantic import BaseModel, Field

# TODO: Make schemas provider-agnostic

class TwilioConfigurationRequest(BaseModel):
    """Request schema for Twilio configuration."""

    provider: str = Field(default="twilio")
    account_sid: str = Field(..., description="Twilio Account SID")
    auth_token: str = Field(..., description="Twilio Auth Token")
    from_numbers: List[str] = Field(
        ..., min_length=1, description="List of Twilio phone numbers"
    )


class TwilioConfigurationResponse(BaseModel):
    """Response schema for Twilio configuration with masked sensitive fields."""

    provider: str
    account_sid: str  # Masked (e.g., "****************def0")
    auth_token: str  # Masked (e.g., "****************abc1")
    from_numbers: List[str]


class TelephonyConfigurationResponse(BaseModel):
    """Top-level telephony configuration response."""

    twilio: TwilioConfigurationResponse | None = None
