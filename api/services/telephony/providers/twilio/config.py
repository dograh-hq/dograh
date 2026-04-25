"""Twilio telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class TwilioConfigurationRequest(BaseModel):
    """Request schema for Twilio configuration."""

    provider: Literal["twilio"] = Field(default="twilio")
    account_sid: str = Field(..., description="Twilio Account SID")
    auth_token: str = Field(..., description="Twilio Auth Token")
    from_numbers: List[str] = Field(
        ..., min_length=1, description="List of Twilio phone numbers"
    )


class TwilioConfigurationResponse(BaseModel):
    """Response schema for Twilio configuration with masked sensitive fields."""

    provider: Literal["twilio"] = Field(default="twilio")
    account_sid: str  # Masked (e.g., "****************def0")
    auth_token: str  # Masked (e.g., "****************abc1")
    from_numbers: List[str]
