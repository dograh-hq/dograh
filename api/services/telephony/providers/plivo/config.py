"""Plivo telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PlivoConfigurationRequest(BaseModel):
    """Request schema for Plivo configuration."""

    provider: Literal["plivo"] = Field(default="plivo")
    auth_id: str = Field(..., description="Plivo Auth ID")
    auth_token: str = Field(..., description="Plivo Auth Token")
    application_id: Optional[str] = Field(
        default=None,
        description=(
            "Plivo Application ID. The application's answer_url is updated "
            "when inbound workflows are attached to numbers on this account. "
            "If omitted, an application is auto-created on save and its id "
            "is stored on the configuration."
        ),
    )
    from_numbers: List[str] = Field(
        default_factory=list, description="List of Plivo phone numbers"
    )


class PlivoConfigurationResponse(BaseModel):
    """Response schema for Plivo configuration with masked sensitive fields."""

    provider: Literal["plivo"] = Field(default="plivo")
    auth_id: str  # Masked
    auth_token: str  # Masked
    application_id: Optional[str] = None
    from_numbers: List[str]
