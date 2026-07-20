"""ARI (Asterisk REST Interface) telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class VicidialAgentAPIConfiguration(BaseModel):
    """VICIdial remote-agent call-control API configuration."""

    url: str = Field(..., min_length=1, description="Full URL to agc/api.php")
    username: str = Field(..., min_length=1, description="VICIdial agent API user")
    password: str = Field(..., min_length=1, description="VICIdial agent API password")
    source: str = Field(default="dograh", description="VICIdial API source tag")

    @field_validator("url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("VICIdial agent API URL must use http:// or https://")
        return stripped


class VicidialNonAgentAPIConfiguration(BaseModel):
    """Optional VICIdial non-agent API configuration for lead updates."""

    url: Optional[str] = Field(default=None, description="Full non_agent_api.php URL")
    username: Optional[str] = Field(default=None, description="Non-agent API user")
    password: Optional[str] = Field(default=None, description="Non-agent API password")
    source: str = Field(default="dograh", description="Non-agent API source tag")

    @field_validator("url")
    @classmethod
    def validate_http_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if stripped and not stripped.startswith(("http://", "https://")):
            raise ValueError("VICIdial non-agent API URL must use http:// or https://")
        return stripped or None

    @model_validator(mode="after")
    def validate_complete_credentials(self):
        supplied = [self.url, self.username, self.password]
        if any(supplied) and not all(supplied):
            raise ValueError(
                "VICIdial non-agent API URL, username, and password must be "
                "configured together"
            )
        return self


class VicidialExternalPBXConfiguration(BaseModel):
    """External-PBX configuration used by the VICIdial strategy adapter."""

    type: Literal["vicidial"] = Field(default="vicidial")
    agent_api: VicidialAgentAPIConfiguration
    non_agent_api: Optional[VicidialNonAgentAPIConfiguration] = None
    timeout_seconds: int = Field(default=8, ge=1, le=30)

    @model_validator(mode="after")
    def drop_empty_non_agent_configuration(self):
        if self.non_agent_api is not None and not self.non_agent_api.url:
            self.non_agent_api = None
        return self


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
    external_pbx: Optional[VicidialExternalPBXConfiguration] = Field(
        default=None,
        description="Optional external PBX connected through this Asterisk instance",
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
    external_pbx: Optional[VicidialExternalPBXConfiguration] = None
    from_numbers: List[str]
