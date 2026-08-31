"""Plivo telephony configuration schemas."""

from typing import Literal, Optional

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
