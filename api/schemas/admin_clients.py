"""Request/response schemas for the superuser admin Clients endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from api.schemas.kyc import KycStatusResponse


class AdminClientItem(BaseModel):
    organization_id: int
    organization_name: str
    owner_user_id: Optional[int] = None
    owner_email: Optional[str] = None
    owner_provider_id: Optional[str] = None
    created_at: Optional[datetime] = None
    voicelink_status: Optional[str] = None
    voicelink_client_id: Optional[str] = None
    voicelink_username: Optional[str] = None
    voicelink_error: Optional[str] = None
    has_voicelink_config: bool = False
    did_number: Optional[str] = None
    # Live reconciliation against VoiceLink (GET /v1/reseller/clients):
    # "active" (exists in VoiceLink) | "missing" | "unconfigured" (reseller
    # creds unset) | "unknown" (reseller lookup failed → stored status shown).
    live_state: str = "unknown"
    live_client_id: Optional[str] = None
    # Remaining call-seconds balance; None = unmetered (unlimited).
    credits_seconds_remaining: Optional[int] = None


class AdminClientsListResponse(BaseModel):
    clients: List[AdminClientItem]


class RetryProvisionRequest(BaseModel):
    """A NEW VoiceLink password — client passwords are never stored locally."""

    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class RetryProvisionResponse(BaseModel):
    voicelink_status: str
    voicelink_client_id: Optional[str] = None
    voicelink_username: Optional[str] = None
    voicelink_error: Optional[str] = None


class CreateClientRequest(BaseModel):
    """Optional password override for one-click create.

    Normally omitted — the endpoint reuses the org's stored (encrypted)
    signup password. A password is only supplied for legacy orgs that have no
    stored secret.
    """

    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class CreateClientResponse(BaseModel):
    action: str  # "linked" (already existed) | "created"
    voicelink_status: str
    voicelink_client_id: Optional[str] = None
    voicelink_username: Optional[str] = None
    voicelink_error: Optional[str] = None


class AssignDidRequest(BaseModel):
    did_number: str = Field(
        ...,
        min_length=1,
        description="DID in its VoiceLink-registered form (e.g. 919484959244)",
    )
    client_id: Optional[str] = Field(
        default=None,
        description=(
            "VoiceLink client id to stamp on the configuration; defaults to "
            "the org's provisioned voicelink_client_id"
        ),
    )


class AssignDidResponse(BaseModel):
    configuration_id: int
    created: bool
    did_number: str
    client_id: Optional[str] = None


class GrantCreditsRequest(BaseModel):
    """Top-up for a metered org's call-credits balance (1 credit = 1 minute)."""

    minutes: int = Field(
        ...,
        ge=1,
        le=100_000,
        description="Minutes of call credit to grant (converted to seconds).",
    )


class GrantCreditsResponse(BaseModel):
    organization_id: int
    granted_seconds: int
    # Balance after the grant; never None here (unmetered orgs are rejected).
    credits_seconds_remaining: Optional[int] = None


class AdminKycStatusResponse(KycStatusResponse):
    """Per-org KYC status for the admin Clients view.

    Same shape as the self-serve ``GET /kyc/status`` response plus a
    ``status`` discriminator:

    - ``ok`` — the org's VoiceLink ``client_id`` resolved and the KYC status
      was fetched from VoiceLink.
    - ``no_client`` — the org has no resolvable VoiceLink client id (KYC
      would act on the reseller's own account, so we don't fetch).
    - ``disabled`` — reseller credentials are not configured.
    """

    status: str = "ok"
    client_id: Optional[str] = None
