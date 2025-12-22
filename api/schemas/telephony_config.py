from typing import List, Optional

from pydantic import BaseModel, Field


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


class VonageConfigurationRequest(BaseModel):
    """Request schema for Vonage configuration."""

    provider: str = Field(default="vonage")
    api_key: Optional[str] = Field(None, description="Vonage API Key")
    api_secret: Optional[str] = Field(None, description="Vonage API Secret")
    application_id: str = Field(..., description="Vonage Application ID")
    private_key: str = Field(..., description="Private key for JWT generation")
    from_numbers: List[str] = Field(
        ..., min_length=1, description="List of Vonage phone numbers (without + prefix)"
    )


class VonageConfigurationResponse(BaseModel):
    """Response schema for Vonage configuration with masked sensitive fields."""

    provider: str
    application_id: str  # Not sensitive, can show full
    api_key: Optional[str]  # Masked if present
    api_secret: Optional[str]  # Masked if present
    private_key: str  # Masked (shows only if configured)
    from_numbers: List[str]


class VobizConfigurationRequest(BaseModel):
    """Request schema for Vobiz configuration."""

    provider: str = Field(default="vobiz")
    auth_id: str = Field(..., description="Vobiz Account ID (e.g., MA_SYQRLN1K)")
    auth_token: str = Field(..., description="Vobiz Auth Token")
    from_numbers: List[str] = Field(
        ...,
        min_length=1,
        description="List of Vobiz phone numbers (E.164 without + prefix)",
    )


class VobizConfigurationResponse(BaseModel):
    """Response schema for Vobiz configuration with masked sensitive fields."""

    provider: str
    auth_id: str  # Masked (e.g., "****************L1NK")
    auth_token: str  # Masked (e.g., "****************KEFO")
    from_numbers: List[str]


class CloudonixConfigurationRequest(BaseModel):
    """Request schema for Cloudonix configuration."""

    provider: str = Field(default="cloudonix")
    bearer_token: str = Field(..., description="Cloudonix API Bearer Token")
    domain_id: str = Field(..., description="Cloudonix Domain ID")
    from_numbers: List[str] = Field(
        default_factory=list, description="List of Cloudonix phone numbers (optional)"
    )


class CloudonixConfigurationResponse(BaseModel):
    """Response schema for Cloudonix configuration with masked sensitive fields."""

    provider: str
    bearer_token: str  # Masked (e.g., "****************abc1")
    domain_id: str  # Not sensitive, can show full
    from_numbers: List[str]


class TelephonyConfigurationResponse(BaseModel):
    """Top-level telephony configuration response."""

    twilio: Optional[TwilioConfigurationResponse] = None
    vonage: Optional[VonageConfigurationResponse] = None
    vobiz: Optional[VobizConfigurationResponse] = None
    cloudonix: Optional[CloudonixConfigurationResponse] = None
