"""WhatsApp telephony configuration schemas.

This module defines Pydantic models for WhatsApp Business API configuration,
following Dograh's telephony provider configuration pattern.
"""

from datetime import UTC, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

DEFAULT_WHATSAPP_PERMISSION_MESSAGE = (
    "Hi! We'd like to speak with you over a quick WhatsApp call. Please tap 'Allow' below to connect with us."
)


def parse_whatsapp_expiration(expiration: Any) -> Optional[datetime]:
    """Parse WhatsApp permission expiration timestamp into a UTC datetime.

    Handles unix integer/float timestamps, numeric strings, and ISO-8601 strings.
    Returns None on invalid or empty values safely without raising.
    """
    if not expiration:
        return None
    if isinstance(expiration, datetime):
        return expiration if expiration.tzinfo else expiration.replace(tzinfo=UTC)
    try:
        ts = float(expiration)
        return datetime.fromtimestamp(ts, tz=UTC)
    except (ValueError, TypeError, OverflowError):
        pass
    if isinstance(expiration, str):
        try:
            cleaned = expiration.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except Exception:
            pass
    return None


# Canonical permission states persisted in whatsapp_call_permissions.status.
GRANTED_PERMISSION_STATUSES = frozenset({"granted_temporary", "granted_permanent"})

# Consent that was refused or taken back. Distinct from "no_permission", which
# means the recipient has simply not answered yet: these fail a parked campaign
# run outright, that one leaves it parked.
REVOKED_PERMISSION_STATUSES = frozenset({"denied", "revoked"})

# Meta reports permission state under several spellings depending on whether it
# comes from the Graph API permission object or a user_call_permissions webhook.
# Map every accepted alias onto the canonical state we store. Anything missing
# from this map is an unknown or future value and must not be persisted.
_PERMISSION_STATUS_ALIASES = {
    "granted": "granted_temporary",
    "temporary": "granted_temporary",
    "granted_temporary": "granted_temporary",
    "permanent": "granted_permanent",
    "granted_permanent": "granted_permanent",
    # "no_permission" means the recipient has not granted yet; it is distinct
    # from an explicit "denied", which fails parked campaign runs outright.
    "no_permission": "no_permission",
    "denied": "denied",
    "revoked": "revoked",
    "expired": "expired",
    "pending": "pending",
}


def normalize_whatsapp_permission_status(status: Any) -> Optional[str]:
    """Map a Meta-reported permission status onto a canonical stored state.

    Returns None for missing or unrecognised values, so callers can skip the
    write rather than overwriting a valid record with a placeholder.
    """
    if not isinstance(status, str):
        return None
    return _PERMISSION_STATUS_ALIASES.get(status.strip().lower())


def is_granted_permission_status(status: Any) -> bool:
    """Whether ``status`` means the recipient has consented to being called.

    Takes a raw Meta status or an already-canonical one - it normalises first,
    so callers do not each keep their own list of the spellings Meta uses
    ("granted", "temporary", "permanent", ...). Adding or renaming a Meta
    status is then one edit to the alias map above, not a hunt through every
    webhook and polling path for a hardcoded tuple.
    """
    return normalize_whatsapp_permission_status(status) in GRANTED_PERMISSION_STATUSES


def is_revoked_permission_status(status: Any) -> bool:
    """Whether ``status`` means consent was refused or withdrawn."""
    return normalize_whatsapp_permission_status(status) in REVOKED_PERMISSION_STATUSES


class WhatsAppConfigurationRequest(BaseModel):
    """Request schema for WhatsApp configuration.
    
    This schema validates incoming configuration save requests and
    integrates with Dograh's metadata-driven UI forms.
    
    Attributes:
        provider: Literal discriminator for union typing
        access_token: WhatsApp Business API access token
        phone_number_id: Business phone number ID from Meta
        webhook_verify_token: Token for webhook verification
        app_secret: App secret for webhook signature validation
        business_initiated_calls_enabled: Enable outbound calling
        call_icon_visibility: Control call icon display in WhatsApp
        default_permission_message: Custom body text for call permission request messages
    """
    
    provider: Literal["whatsapp"] = Field(default="whatsapp")
    access_token: str = Field(..., min_length=1, description="WhatsApp Business API access token")
    phone_number_id: str = Field(..., min_length=1, description="Business phone number ID")
    webhook_verify_token: str = Field(..., min_length=1, description="Webhook verification token")
    app_secret: str = Field(..., min_length=1, description="App secret for webhook signature validation")
    business_initiated_calls_enabled: bool = Field(
        default=False,
        description="Enable business-initiated calls to WhatsApp users"
    )
    call_icon_visibility: Literal["enabled", "disabled", "business_hours"] = Field(
        default="enabled",
        description="Control when call icon appears to users"
    )
    default_permission_message: Optional[str] = Field(
        default=None,
        description="Custom body text sent with call permission requests"
    )


class WhatsAppConfigurationResponse(BaseModel):
    """Response schema for WhatsApp configuration.

    This schema defines the masked response returned to the UI,
    ensuring sensitive credentials are never exposed unmasked.
    """

    provider: Literal["whatsapp"] = Field(default="whatsapp")
    phone_number_id: str
    access_token: Optional[str] = None
    app_secret: Optional[str] = None
    webhook_verify_token: Optional[str] = None
    business_initiated_calls_enabled: bool = False
    call_icon_visibility: Literal["enabled", "disabled", "business_hours"] = "enabled"
    default_permission_message: Optional[str] = None
