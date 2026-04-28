"""ARI (Asterisk REST Interface) telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class ARIConfigurationRequest(BaseModel):
    """Request schema for Asterisk ARI configuration."""

    provider: Literal["ari"] = Field(default="ari")
    ari_endpoint: str = Field(
        ..., description="ARI base URL (e.g., http://asterisk.example.com:8088)"
    )
    app_name: str = Field(
        ..., description="Stasis application name registered in Asterisk"
    )
    app_password: str = Field(..., description="ARI user password")
    ws_client_name: str = Field(
        default="",
        description="websocket_client.conf connection name for externalMedia (e.g., dograh_staging)",
    )
    from_numbers: List[str] = Field(
        default_factory=list,
        description="List of SIP extensions/numbers for outbound calls (optional)",
    )


class ARIConfigurationResponse(BaseModel):
    """Response schema for ARI configuration with masked sensitive fields."""

    provider: Literal["ari"] = Field(default="ari")
    ari_endpoint: str
    app_name: str
    app_password: str  # Masked
    ws_client_name: str = ""
    from_numbers: List[str]
