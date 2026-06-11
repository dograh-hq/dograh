"""Request/response schemas for the superuser admin Clients endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


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
