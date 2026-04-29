"""Telephony address normalization.

Telephony "from" / "to" identifiers can be PSTN numbers (E.164 or local),
SIP URIs, or bare SIP extensions. This module normalizes any input to a
canonical form used both for storage in `telephony_phone_numbers.address_normalized`
and for lookups against incoming webhooks.

The canonical form is deterministic and case-insensitive where the
underlying protocol allows it.

Lives in ``api.utils`` (not ``api.services.telephony``) so it can be
imported from migrations and DB clients without triggering provider
registration in the telephony package's ``__init__.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

from api.utils.telephony_helper import get_country_code

AddressType = Literal["pstn", "sip_uri", "sip_extension"]

_PSTN_DIGITS_RE = re.compile(r"^\d{8,15}$")
_PSTN_STRIP_RE = re.compile(r"[\s\-\(\)]")
# RFC 3261 SIP URI: sip:user@host[:port][;params][?headers]
# We only normalize scheme, host, port, and the user part (preserving case).
_SIP_URI_RE = re.compile(
    r"^(?P<scheme>sips?):(?:(?P<user>[^@;?]+)@)?(?P<host>[^:;?]+)"
    r"(?::(?P<port>\d+))?(?P<rest>[;?].*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizedAddress:
    canonical: str
    address_type: AddressType
    country_code: Optional[str] = None  # ISO-2; only set for PSTN when known


def normalize_telephony_address(
    raw: str, country_hint: Optional[str] = None
) -> NormalizedAddress:
    """Normalize a telephony address into a canonical form for storage/lookup.

    `country_hint` is an ISO-2 country code used to disambiguate non-E.164
    PSTN inputs (e.g. "08043071383" with hint "IN" → "+918043071383").
    """
    if raw is None:
        raise ValueError("address must not be None")

    raw = raw.strip()
    if not raw:
        raise ValueError("address must not be empty")

    lowered = raw.lower()
    if lowered.startswith(("sip:", "sips:")):
        return _normalize_sip_uri(raw)

    digits = _PSTN_STRIP_RE.sub("", raw)
    if digits.startswith("+"):
        digits = digits[1:]
    if _PSTN_DIGITS_RE.fullmatch(digits):
        return _normalize_pstn(digits, country_hint)

    # Anything else — short numeric extension, alphanumeric username, etc.
    return NormalizedAddress(canonical=raw.lower(), address_type="sip_extension")


def _normalize_pstn(digits: str, country_hint: Optional[str]) -> NormalizedAddress:
    country_code: Optional[str] = None

    # If a country hint is given and the digits don't already start with that
    # country's dial code, try to apply it. Local numbers may include a leading
    # zero that needs stripping (e.g. India "0xxxx" → "+91xxxx").
    if country_hint:
        dial = get_country_code(country_hint)
        if dial:
            country_code = country_hint.upper()
            if not digits.startswith(dial):
                stripped = digits.lstrip("0")
                # Only apply the hint if doing so yields a sane E.164 length.
                candidate = f"{dial}{stripped}"
                if 8 <= len(candidate) <= 15:
                    digits = candidate

    return NormalizedAddress(
        canonical=f"+{digits}",
        address_type="pstn",
        country_code=country_code,
    )


def _normalize_sip_uri(raw: str) -> NormalizedAddress:
    m = _SIP_URI_RE.match(raw)
    if not m:
        # Malformed URI — preserve as-is, lowercased, so equality still works.
        return NormalizedAddress(canonical=raw.lower(), address_type="sip_uri")

    scheme = m.group("scheme").lower()
    user = m.group("user")  # case-preserving per RFC 3261
    host = m.group("host").lower()
    port = m.group("port")
    rest = m.group("rest") or ""

    # Drop default ports (5060 for sip, 5061 for sips).
    if (scheme == "sip" and port == "5060") or (scheme == "sips" and port == "5061"):
        port = None

    canonical = f"{scheme}:"
    if user:
        canonical += f"{user}@"
    canonical += host
    if port:
        canonical += f":{port}"
    if rest:
        canonical += rest.lower()

    return NormalizedAddress(canonical=canonical, address_type="sip_uri")
