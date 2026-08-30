"""Cloudonix telephony configuration schemas."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .regions import CLOUDONIX_REGION_NAMES, get_cloudonix_region


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


_TRUNK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


class CloudonixOutboundTrunkConfiguration(BaseModel):
    """Dograh-managed Cloudonix outbound SIP trunk.

    Only the trunk name and the SIP domain are operator-supplied. The remote
    peer (IP, port, transport) is derived from ``region`` when the Cloudonix
    payload is built, so the trunk always terminates on the same regional edge
    the customer sees under SIP connectivity.
    """

    id: str | None = Field(
        default=None,
        description=(
            "Dograh-owned identifier for this trunk, minted on first save. "
            "Stable across renames, and the key the Cloudonix trunk UUID is "
            "stored under. Clients round-trip it; they never invent it."
        ),
    )
    enabled: bool = False
    name: str | None = Field(
        default=None,
        description=(
            "Unique name for the Cloudonix voice trunk. Letters, digits and "
            "hyphens only — Cloudonix trunk names cannot contain spaces."
        ),
    )
    region: str | None = Field(
        default=None,
        description=(
            "Cloudonix region whose SIP edge terminates this trunk; sets the "
            "remote IP, port and transport."
        ),
    )
    sip_domain: str | None = Field(
        default=None,
        description=(
            "Domain Cloudonix puts in both the SIP To header and the SIP "
            "Request-URI for calls on this trunk."
        ),
    )

    @field_validator("id", "name", "region", "sip_domain")
    @classmethod
    def _strip_optional_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("name")
    @classmethod
    def _reject_spaces_in_name(cls, value: str | None) -> str | None:
        if value is not None and not _TRUNK_NAME_PATTERN.match(value):
            raise ValueError(
                "Outbound trunk name may only contain letters, digits and hyphens"
            )
        return value

    @field_validator("region")
    @classmethod
    def _known_region(cls, value: str | None) -> str | None:
        if value is None:
            return None
        region = get_cloudonix_region(value)
        if region is None:
            raise ValueError(
                f"Unknown Cloudonix region '{value}'. Expected one of: "
                + ", ".join(CLOUDONIX_REGION_NAMES)
            )
        return region.name

    @model_validator(mode="after")
    def _require_fields_when_enabled(self):
        if self.enabled and (not self.name or not self.region or not self.sip_domain):
            raise ValueError(
                "Outbound trunk name, region and SIP domain are required when "
                "outbound trunk setup is enabled"
            )
        return self


def _require_distinct_trunks(
    trunks: list[CloudonixOutboundTrunkConfiguration],
) -> list[CloudonixOutboundTrunkConfiguration]:
    """Ids key the stored Cloudonix UUIDs; names are unique within a domain."""
    ids = [trunk.id for trunk in trunks if trunk.id]
    if len(set(ids)) != len(ids):
        raise ValueError("Outbound trunk ids must be unique")

    names = [trunk.name for trunk in trunks if trunk.name]
    if len(set(names)) != len(names):
        raise ValueError("Outbound trunk names must be unique")
    return trunks


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
    outbound_trunks: list[CloudonixOutboundTrunkConfiguration] = Field(
        default_factory=list,
        description=(
            "Outbound SIP trunks Dograh creates and keeps in sync on this "
            "Cloudonix domain. Trunks dropped from the list are deactivated. "
            "The UI manages a single trunk today; the list is the storage "
            "shape so more can be added without a schema change."
        ),
    )

    from_numbers: list[str] = Field(
        default_factory=list, description="List of Cloudonix phone numbers (optional)"
    )

    @field_validator("outbound_trunks")
    @classmethod
    def _distinct_trunks(
        cls, value: list[CloudonixOutboundTrunkConfiguration]
    ) -> list[CloudonixOutboundTrunkConfiguration]:
        return _require_distinct_trunks(value)


class CloudonixConfigurationResponse(BaseModel):
    """Response schema for Cloudonix configuration with masked sensitive fields.

    Server-managed credential fields (``domain_uuid``, ``provisioning_id``,
    ``managed_by``, the application and trunk UUIDs) are stripped before this
    is built — they are Dograh's bookkeeping, not something a client sends
    back or renders.
    """

    provider: Literal["cloudonix"] = Field(default="cloudonix")
    bearer_token: str  # Masked
    domain_id: str
    application_name: str | None = None
    outbound_trunks: list[CloudonixOutboundTrunkConfiguration] = Field(
        default_factory=list
    )
    from_numbers: list[str]
