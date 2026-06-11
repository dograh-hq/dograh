"""Request/response schemas for the VoiceLink KYC routes."""

from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class KycStep1Request(BaseModel):
    """Step 1 — register account details."""

    term_and_condition: bool = Field(
        ..., description="Must be true — acceptance of VoiceLink's terms."
    )
    account_type: Literal["individual", "business"]
    business_name: Optional[str] = Field(
        default=None, description="Required when account_type is 'business'."
    )
    full_name: str
    email: str
    phone: str
    billing_address: str

    @model_validator(mode="after")
    def _require_business_name(self) -> "KycStep1Request":
        if self.account_type == "business" and not self.business_name:
            raise ValueError(
                "business_name is required when account_type is 'business'"
            )
        return self


class KycStep2Request(BaseModel):
    """Step 2 — PAN verification."""

    pan_holder_name: str
    pan_number: str


class KycStep3Request(BaseModel):
    """Step 3 — Aadhaar verification via DigiLocker."""

    redirect_url: Optional[str] = Field(
        default=None,
        description="URL DigiLocker redirects back to after verification.",
    )


class KycStep4Request(BaseModel):
    """Step 4 — GST verification (business accounts only)."""

    gst_number: str


class KycStatusResponse(BaseModel):
    """KYC status for the caller's organization.

    ``enabled`` is false when the reseller credentials are not configured in
    the environment — the UI then shows a "KYC not configured" state.
    """

    enabled: bool
    client_id_configured: bool = False
    has_voicelink_config: bool = False
    kyc_status: Optional[str] = None
    pan_verified: Optional[bool] = None
    aadhaar_verified: Optional[bool] = None
    gst_verified: Optional[bool] = None
    is_complete: Optional[bool] = None
    current_step: Optional[Union[int, str]] = None
    account_type: Optional[str] = None


class KycActionResponse(BaseModel):
    """Pass-through of a VoiceLink KYC step response."""

    message: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
