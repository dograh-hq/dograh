"""Vobiz telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class VobizConfigurationRequest(BaseModel):
    """Request schema for Vobiz configuration."""

    provider: Literal["vobiz"] = Field(default="vobiz")
    auth_id: str = Field(..., description="Vobiz Account ID (e.g., MA_SYQRLN1K)")
    auth_token: str = Field(..., description="Vobiz Auth Token")
    application_id: str = Field(
        ...,
        description=(
            "Vobiz Application ID. The application's answer_url is updated "
            "when inbound workflows are attached to numbers on this account."
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
    application_id: str
    from_numbers: List[str]
