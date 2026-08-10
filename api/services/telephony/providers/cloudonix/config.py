"""Cloudonix telephony configuration schemas."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_cloudonix_domain(value: str | None) -> str | None:
    """Normalize legacy short names while preserving custom FQDN domains."""
    if value is None:
        return None
    value = value.strip().rstrip(".").lower()
    if not value:
        return value
    if "." in value:
        return value
    return f"{value}.cloudonix.net"


class CloudonixOutboundTrunkAuthentication(BaseModel):
    """Optional SIP digest credentials for the remote termination peer."""

    username: str | None = None
    password: str | None = None
    overwrite_from: bool = Field(
        default=False,
        description=(
            "Use the authentication username as the SIP From caller ID. "
            "Cloudonix sends this as profile.authentication.overwrite-from."
        ),
    )

    @field_validator("username", "password")
    @classmethod
    def _strip_optional_auth_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _require_complete_credentials(self):
        if bool(self.username) != bool(self.password):
            raise ValueError(
                "Outbound trunk authentication username and password must be "
                "provided together"
            )
        return self


class CloudonixOutboundTrunkProfile(BaseModel):
    """Cloudonix-recognized outbound voice-trunk profile fields."""

    hostname: str | None = Field(
        default=None,
        description="Pin calls to one Cloudonix Border Gateway hostname or IP",
    )
    domain: str | None = Field(
        default=None,
        description="Override the domain in the SIP To header",
    )
    ruri_domain: str | None = Field(
        default=None,
        description="Override the SIP Request-URI domain",
    )
    connection_timeout: int | None = Field(default=None, ge=1)
    provisional_timeout: int | None = Field(default=None, ge=1)
    authentication: CloudonixOutboundTrunkAuthentication | None = None

    @field_validator("hostname", "domain", "ruri_domain")
    @classmethod
    def _strip_optional_profile_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class CloudonixOutboundTrunkConfiguration(BaseModel):
    """Dograh-managed Cloudonix outbound SIP trunk."""

    enabled: bool = False
    name: str | None = Field(
        default=None,
        description="Unique human-readable name for the Cloudonix voice trunk",
    )
    ip: str | None = Field(
        default=None,
        description="Remote carrier/PBX IP address or FQDN",
    )
    port: int = Field(default=5060, ge=1, le=65535)
    transport: Literal["udp", "tcp", "tls"] = "udp"
    prefix: str = Field(
        default="",
        description="Technical prefix Cloudonix prepends to the dialed destination",
    )
    profile: CloudonixOutboundTrunkProfile | None = None

    @field_validator("name", "ip")
    @classmethod
    def _strip_required_when_enabled(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("prefix")
    @classmethod
    def _strip_prefix(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _require_peer_when_enabled(self):
        if self.enabled and (not self.name or not self.ip):
            raise ValueError(
                "Outbound trunk name and remote SIP address are required when "
                "outbound trunk setup is enabled"
            )
        return self


class CloudonixConfigurationRequest(BaseModel):
    """Request schema for Cloudonix configuration."""

    provider: Literal["cloudonix"] = Field(default="cloudonix")
    bearer_token: str = Field(..., description="Cloudonix API Bearer Token")
    domain_id: str = Field(..., description="Cloudonix domain name")

    @field_validator("domain_id")
    @classmethod
    def _normalize_domain_id(cls, v: str) -> str:
        return normalize_cloudonix_domain(v) or ""

    application_name: str | None = Field(
        default=None,
        description=(
            "Cloudonix Voice Application name. The application's url is "
            "updated when inbound workflows are attached to numbers on "
            "this domain. If omitted, an application is auto-created on "
            "save and its name is stored on the configuration."
        ),
    )
    outbound_trunk: CloudonixOutboundTrunkConfiguration | None = Field(
        default=None,
        description=(
            "Optional outbound SIP trunk that Dograh creates and keeps in sync "
            "on this Cloudonix domain"
        ),
    )
    from_numbers: list[str] = Field(
        default_factory=list, description="List of Cloudonix phone numbers (optional)"
    )


class CloudonixConfigurationResponse(BaseModel):
    """Response schema for Cloudonix configuration with masked sensitive fields."""

    provider: Literal["cloudonix"] = Field(default="cloudonix")
    bearer_token: str  # Masked
    domain_id: str
    domain_uuid: str | None = Field(
        default=None,
        description="Cloudonix domain UUID fetched automatically from domainGet",
    )
    application_name: str | None = None
    outbound_trunk: CloudonixOutboundTrunkConfiguration | None = None
    outbound_trunk_uuid: str | None = Field(
        default=None,
        description="UUID of the Dograh-managed Cloudonix outbound voice trunk",
    )
    from_numbers: list[str]
