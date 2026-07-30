"""TryVox telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TryVoxConfigurationRequest(BaseModel):
    """Credentials and optional inbound application used by TryVox."""

    provider: Literal["tryvox"] = Field(default="tryvox")
    auth_id: str = Field(..., description="TryVox account Auth ID")
    auth_token: str = Field(..., description="TryVox account Auth Token")
    webhook_secret: str = Field(
        ..., description="Per-account secret used to verify TryVox voice webhooks"
    )
    application_id: Optional[str] = Field(
        default=None,
        description="TryVox Voice Application ID used for inbound number routing",
    )
    api_base_url: str = Field(
        default="https://api.tryvox.io",
        description="TryVox API base URL",
    )
    from_numbers: List[str] = Field(
        default_factory=list,
        description="TryVox phone numbers available for outbound calls",
    )


class TryVoxConfigurationResponse(BaseModel):
    """TryVox configuration with secrets masked by the shared config API."""

    provider: Literal["tryvox"] = Field(default="tryvox")
    auth_id: str
    auth_token: str
    webhook_secret: str
    application_id: Optional[str] = None
    api_base_url: str = "https://api.tryvox.io"
    from_numbers: List[str]
