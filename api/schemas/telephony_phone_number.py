"""Request/response schemas for the phone-number CRUD endpoints."""

import re
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Mirrors the regexes in api/utils/telephony_address.py — keep in sync.
_ADDRESS_FORMAT_STRIP_RE = re.compile(r"[\s\-()]")
_ADDRESS_E164_RE = re.compile(r"^\+\d{8,15}$")
_ADDRESS_BARE_DIGITS_RE = re.compile(r"^\d{8,15}$")


class SmartVoicemailConfig(BaseModel):
    """Per-number "smart voicemail" screening configuration.

    Stored under ``extra_metadata["smart_voicemail"]`` on the phone-number row
    (no dedicated columns). When ``enabled``, an inbound call to this number is
    first forwarded to ``forward_to_number``; if a human answers the caller is
    bridged to them, and if it goes to voicemail (or isn't answered within
    ``ring_timeout_seconds``) the AI workflow takes the call instead.
    """

    enabled: bool = False
    # Designated human number to ring first. Required when ``enabled``.
    forward_to_number: Optional[str] = Field(default=None, max_length=32)
    # How long to ring the human before giving up and handing to the AI.
    ring_timeout_seconds: int = Field(default=25, ge=5, le=120)

    @model_validator(mode="after")
    def _validate(self) -> "SmartVoicemailConfig":
        if not self.enabled:
            return self
        if not self.forward_to_number:
            raise ValueError(
                "forward_to_number is required when smart voicemail is enabled"
            )
        stripped = _ADDRESS_FORMAT_STRIP_RE.sub("", self.forward_to_number.strip())
        if not _ADDRESS_E164_RE.fullmatch(stripped):
            raise ValueError(
                "forward_to_number must be E.164 (e.g. '+14155551234')"
            )
        # Normalize to the stripped E.164 form so downstream callers don't have
        # to re-clean it.
        self.forward_to_number = stripped
        return self


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
    # Folded into ``extra_metadata["smart_voicemail"]`` by the route handler.
    smart_voicemail: Optional[SmartVoicemailConfig] = None

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
    # When provided, merged into ``extra_metadata["smart_voicemail"]`` by the
    # route handler (preserving other extra_metadata keys).
    smart_voicemail: Optional[SmartVoicemailConfig] = None


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
    # Typed view of ``extra_metadata["smart_voicemail"]`` for the UI; the
    # source of truth remains ``extra_metadata``.
    smart_voicemail: Optional[SmartVoicemailConfig] = None
    created_at: datetime
    updated_at: datetime
    # Only set on create/update responses when the route attempted a
    # provider-side sync (e.g. setting Twilio's VoiceUrl). Omitted on reads.
    provider_sync: Optional[ProviderSyncStatus] = None

    @model_validator(mode="after")
    def _derive_smart_voicemail(self) -> "PhoneNumberResponse":
        if self.smart_voicemail is None and self.extra_metadata:
            raw = self.extra_metadata.get("smart_voicemail")
            if isinstance(raw, dict):
                try:
                    self.smart_voicemail = SmartVoicemailConfig.model_validate(raw)
                except Exception:
                    # Tolerate legacy/partial metadata — never fail a read.
                    self.smart_voicemail = None
        return self


class PhoneNumberListResponse(BaseModel):
    phone_numbers: list[PhoneNumberResponse]
