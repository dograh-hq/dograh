"""Cloudonix telephony configuration schemas."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .regions import CLOUDONIX_REGION_NAMES, get_cloudonix_region

# Identity of the configuration Dograh provisions for every organization at
# signup. Lives here rather than in ``provisioning`` so the leaf modules that
# only need to recognize a managed row don't pull in the provisioning path.
MANAGED_CONFIGURATION_NAME = "Dograh Cloudonix SIP"
MANAGED_BY = "dograh-mps"


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


def validate_cloudonix_trunk_name(value: str) -> str:
    """Cloudonix trunk names cannot contain spaces."""
    value = value.strip()
    if not _TRUNK_NAME_PATTERN.match(value):
        raise ValueError(
            "Outbound trunk name may only contain letters, digits and hyphens"
        )
    return value


class CloudonixTrunkSettings(BaseModel):
    """Provider-specific settings for one Cloudonix outbound SIP trunk.

    Only the SIP domain is operator-supplied beyond the region. The remote peer
    (IP, port, transport) is derived from ``region`` when the Cloudonix payload
    is built, so the trunk always terminates on the same regional edge the
    customer sees under SIP connectivity.
    """

    region: str = Field(
        description=(
            "Cloudonix region whose SIP edge terminates this trunk; sets the "
            "remote IP, port and transport."
        ),
    )
    sip_domain: str = Field(
        description=(
            "Domain Cloudonix puts in both the SIP To header and the SIP "
            "Request-URI for calls on this trunk."
        ),
    )

    @field_validator("region", "sip_domain")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @field_validator("region")
    @classmethod
    def _known_region(cls, value: str) -> str:
        region = get_cloudonix_region(value)
        if region is None:
            raise ValueError(
                f"Unknown Cloudonix region '{value}'. Expected one of: "
                + ", ".join(CLOUDONIX_REGION_NAMES)
            )
        return region.name


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
