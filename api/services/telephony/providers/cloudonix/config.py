"""Cloudonix telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class CloudonixConfigurationRequest(BaseModel):
    """Request schema for Cloudonix configuration."""

    provider: Literal["cloudonix"] = Field(default="cloudonix")
    bearer_token: str = Field(..., description="Cloudonix API Bearer Token")
    domain_id: str = Field(..., description="Cloudonix Domain ID")
    application_name: str = Field(
        ...,
        description=(
            "Cloudonix Voice Application name. The application's url is "
            "updated when inbound workflows are attached to numbers on "
            "this domain."
        ),
    )
    from_numbers: List[str] = Field(
        default_factory=list, description="List of Cloudonix phone numbers (optional)"
    )


class CloudonixConfigurationResponse(BaseModel):
    """Response schema for Cloudonix configuration with masked sensitive fields."""

    provider: Literal["cloudonix"] = Field(default="cloudonix")
    bearer_token: str  # Masked
    domain_id: str
    application_name: str
    from_numbers: List[str]
