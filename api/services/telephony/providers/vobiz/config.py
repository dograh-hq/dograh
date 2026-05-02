"""Vobiz telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class VobizConfigurationRequest(BaseModel):
    """Request schema for Vobiz configuration."""

    provider: Literal["vobiz"] = Field(default="vobiz")
    auth_id: str = Field(..., description="Vobiz Account ID (e.g., MA_SYQRLN1K)")
    auth_token: str = Field(..., description="Vobiz Auth Token")
    application_id: Optional[str] = Field(
        default=None,
        description=(
            "Vobiz Application ID. The application's answer_url is updated "
            "when inbound workflows are attached to numbers on this account. "
            "If omitted, an application is auto-created on save and its id "
            "is stored on the configuration."
        ),
    )
    from_numbers: List[str] = Field(
        default_factory=list,
        description="List of Vobiz phone numbers (E.164 without + prefix)",
    )


class VobizConfigurationResponse(BaseModel):
    """Response schema for Vobiz configuration with masked sensitive fields."""

    provider: Literal["vobiz"] = Field(default="vobiz")
    auth_id: str  # Masked
    auth_token: str  # Masked
    application_id: Optional[str] = None
    from_numbers: List[str]
