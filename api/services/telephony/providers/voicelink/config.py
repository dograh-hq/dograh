"""VoiceLink telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

DEFAULT_VOICELINK_API_BASE = "https://app.voicelink.co.in/api"


class VoiceLinkConfigurationRequest(BaseModel):
    """Request schema for VoiceLink configuration."""

    provider: Literal["voicelink"] = Field(default="voicelink")
    api_base: str = Field(
        default=DEFAULT_VOICELINK_API_BASE,
        description="VoiceLink API base URL",
    )
    username: Optional[str] = Field(
        default=None,
        description=(
            "VoiceLink account username. Used together with password to "
            "obtain (and refresh) bearer tokens via /v1/auth/login."
        ),
    )
    password: Optional[str] = Field(
        default=None, description="VoiceLink account password"
    )
    bearer_token: Optional[str] = Field(
        default=None,
        description=(
            "Static VoiceLink bearer token. Optional when username/password "
            "are provided — those allow automatic re-login on token expiry."
        ),
    )
    did_number: str = Field(
        ...,
        description=(
            "DID registered with VoiceLink, in its registered form "
            "(e.g. 919484959244). Used as the caller id for outbound dials."
        ),
    )
    from_numbers: List[str] = Field(
        default_factory=list,
        description="List of VoiceLink DID numbers in registered form",
    )
    client_id: Optional[str] = Field(
        default=None,
        description=(
            "VoiceLink client id this configuration belongs to. Optional — "
            "used by the KYC section to scope reseller KYC calls to this "
            "client. When unset, KYC calls act on the reseller's own KYC."
        ),
    )

    @model_validator(mode="after")
    def _require_credentials(self) -> "VoiceLinkConfigurationRequest":
        if not self.bearer_token and not (self.username and self.password):
            raise ValueError(
                "VoiceLink configuration requires either bearer_token or "
                "both username and password"
            )
        return self


class VoiceLinkConfigurationResponse(BaseModel):
    """Response schema for VoiceLink configuration with masked sensitive fields."""

    provider: Literal["voicelink"] = Field(default="voicelink")
    api_base: str = DEFAULT_VOICELINK_API_BASE
    username: Optional[str] = None  # Masked
    password: Optional[str] = None  # Masked
    bearer_token: Optional[str] = None  # Masked
    did_number: str
    from_numbers: List[str]
    client_id: Optional[str] = None
