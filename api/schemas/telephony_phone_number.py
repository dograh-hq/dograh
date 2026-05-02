"""Request/response schemas for the phone-number CRUD endpoints."""

import re
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Mirrors the regexes in api/utils/telephony_address.py — keep in sync.
_ADDRESS_FORMAT_STRIP_RE = re.compile(r"[\s\-()]")
_ADDRESS_E164_RE = re.compile(r"^\+\d{8,15}$")
_ADDRESS_BARE_DIGITS_RE = re.compile(r"^\d{8,15}$")


class PhoneNumberCreateRequest(BaseModel):
    """Create a new phone number under a telephony configuration.

    ``address_normalized`` and ``address_type`` are computed server-side from
    ``address`` (and ``country_code`` if PSTN). ``address`` itself is stored
    verbatim for display.
    """

    address: str = Field(..., min_length=1, max_length=255)
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    label: Optional[str] = Field(default=None, max_length=64)
    inbound_workflow_id: Optional[int] = None
    is_active: bool = True
    is_default_caller_id: bool = False
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_address_shape(self) -> "PhoneNumberCreateRequest":
        """Reject the one shape that produces a broken canonical form:
        8-15 bare digits without a leading "+" and without a country code.

        Without a country hint, ``normalize_telephony_address`` would treat
        such input as PSTN and return a junk E.164 (e.g. "02271264296" →
        "+02271264296"). Either include the "+" and dial code, or pass
        ``country_code`` so the helper can apply the right prefix.

        Other shapes (SIP URIs, short extensions, alphanumerics) are
        intentionally permissive — the address parser handles them.
        """
        raw = self.address.strip()
        # SIP URI: backend parser handles it.
        if raw.lower().startswith(("sip:", "sips:")):
            return self
        stripped = _ADDRESS_FORMAT_STRIP_RE.sub("", raw)
        # E.164 shape — fine without country hint.
        if _ADDRESS_E164_RE.fullmatch(stripped):
            return self
        # 8-15 bare digits — must have country_code, otherwise the
        # canonical form will be wrong.
        if _ADDRESS_BARE_DIGITS_RE.fullmatch(stripped) and not self.country_code:
            raise ValueError(
                "PSTN addresses without a leading '+' need a country_code "
                "(ISO-2, e.g. 'US' or 'IN') so we can produce the right "
                "E.164 form. Either include the country code in the address "
                "(e.g. '+14155551234') or set country_code."
            )
        return self


class PhoneNumberUpdateRequest(BaseModel):
    """Partial update. ``address`` is intentionally immutable — to change a
    number, delete the row and create a new one."""

    label: Optional[str] = Field(default=None, max_length=64)
    inbound_workflow_id: Optional[int] = None
    # Set to true to clear inbound_workflow_id (FK is otherwise non-nullable
    # via the partial-update pattern).
    clear_inbound_workflow: bool = False
    is_active: Optional[bool] = None
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    extra_metadata: Optional[Dict[str, Any]] = None


class ProviderSyncStatus(BaseModel):
    """Result of pushing a phone-number change to the upstream provider.

    Returned alongside create/update responses when the route attempted to
    sync inbound webhook configuration. ``ok=False`` is a warning, not a
    fatal error — the DB write succeeded.
    """

    ok: bool
    message: Optional[str] = None


class PhoneNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    telephony_configuration_id: int
    address: str
    address_normalized: str
    address_type: str
    country_code: Optional[str] = None
    label: Optional[str] = None
    inbound_workflow_id: Optional[int] = None
    inbound_workflow_name: Optional[str] = None
    is_active: bool
    is_default_caller_id: bool
    extra_metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    # Only set on create/update responses when the route attempted a
    # provider-side sync (e.g. setting Twilio's VoiceUrl). Omitted on reads.
    provider_sync: Optional[ProviderSyncStatus] = None


class PhoneNumberListResponse(BaseModel):
    phone_numbers: list[PhoneNumberResponse]
