"""Cloudonix telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CloudonixConfigurationRequest(BaseModel):
    """Request schema for Cloudonix configuration."""

    provider: Literal["cloudonix"] = Field(default="cloudonix")
    bearer_token: str = Field(..., description="Cloudonix API Bearer Token")
    domain_id: str = Field(..., description="Cloudonix Domain ID")

    @field_validator("domain_id")
    @classmethod
    def _normalize_domain_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            return v
        if v.endswith(".cloudonix.net"):
            return v
        return f"{v}.cloudonix.net"

    application_name: Optional[str] = Field(
        default=None,
        description=(
            "Cloudonix Voice Application name. The application's url is "
            "updated when inbound workflows are attached to numbers on "
            "this domain. If omitted, an application is auto-created on "
            "save and its name is stored on the configuration."
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
    application_name: Optional[str] = None
    from_numbers: List[str]
