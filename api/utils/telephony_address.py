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


_E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
# Presentation characters people type into a spreadsheet cell. Removing them
# changes how a number reads, never which number it is.
_E164_FORMATTING_RE = re.compile(r"[\s\-()\.]")
# The national trunk prefix, written in brackets between the country code and
# the subscriber number: "+44 (0) 20 7946 0958". It is the digit you dial
# *instead of* the country code, never alongside it, so it has to be dropped
# rather than have its brackets stripped - "+4402079460958" is a different,
# wrong number that still looks like valid E.164.
#
# Anchored to the country code, which is 1-3 digits: only a "(0)" sitting
# directly after it is the trunk prefix. One appearing later is part of the
# subscriber number's punctuation, and dropping its digit would change who
# gets dialled - so that case falls through to plain bracket stripping.
_TRUNK_PREFIX_RE = re.compile(r"^(\+\s*[0-9]{1,3}[\s\-.]*)\(\s*0\s*\)")


def is_e164(raw: Optional[str]) -> bool:
    """Whether ``raw`` carries an explicit country code in strict E.164 form
    (leading '+', no internal or surrounding punctuation or whitespace, 8-15 digits, non-zero country code).

    The single definition of "this number says which country it is for". Callers
    that must not guess a country - notably the WhatsApp business-initiated-call
    restriction check - gate on this instead of inferring one from leading
    digits, which cannot be done reliably: a bare 11-digit number starting with
    1 is a US number with its country code, or a Chinese mobile without one, and
    nothing in the string distinguishes them.

    Campaign leads are already held to this at ingest (``validate_source_data``
    rejects any row whose phone number lacks a leading ``+``); this is the same
    rule for the paths that do not go through a campaign source.
    """
    if not raw or not isinstance(raw, str):
        return False
    return bool(_E164_RE.fullmatch(raw))


def canonicalize_e164(raw: Optional[str]) -> Optional[str]:
    """Return the strict E.164 form of ``raw``, or None if it is not one.

    Only formatting is removed - spaces, dashes, brackets, dots, and a
    bracketed national trunk prefix - so this converts "+44 7123 456789" to
    "+447123456789" and "+44 (0) 20 7946 0958" to "+442079460958", and refuses
    anything whose country code is missing or ambiguous, exactly like
    ``is_e164``.

    Campaign ingest runs every lead through this before storing it. Ingest
    used to accept any number starting with "+" and keep it verbatim, while
    the WhatsApp dial path requires strict E.164 - so a perfectly valid
    spreadsheet entry was accepted at upload and failed at dial time, with the
    run marked failed before a call was placed. Normalising once at the point
    of entry is what keeps the two rules from disagreeing about the same
    contact.
    """
    if not raw or not isinstance(raw, str):
        return None
    # Drop the trunk digit before the brackets around it are stripped, or it
    # survives as a leading zero on the subscriber number.
    candidate = _TRUNK_PREFIX_RE.sub(r"\1", raw.strip(), count=1)
    candidate = _E164_FORMATTING_RE.sub("", candidate)
    return candidate if is_e164(candidate) else None


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


# Characters that change the meaning of a dial string rather than address a
# destination: whitespace and control characters break the string in two, and
# "&" and "," separate destinations in the dial syntaxes that carry these
# addresses. A destination containing one is a malformed row, not an exotic
# endpoint, whichever provider ends up dialling it.
_UNSAFE_DESTINATION_RE = re.compile(r"[\s&,\x00-\x1f\x7f]")


def validate_destination_address(raw: str, *, require_e164: bool) -> str | None:
    """Check an address that will be dialled.

    Returns ``None`` when the address is dialable, otherwise the requirement it
    breaks, phrased to follow a subject ("must ..."), so a caller can name the
    rows and state the rule once rather than repeating it per row.

    ``require_e164`` is the provider's answer to what it can reach. A carrier
    hands the call to the PSTN and rejects anything that is not a routable
    number, so checking E.164 at upload turns a whole failed campaign into one
    corrected file. A PBX reaches extensions, SIP URIs and its own dial
    strings, none of which are E.164, so there only the characters above make
    an address undialable.
    """
    address = raw.strip()
    if not address:
        return "must not be empty"

    if _UNSAFE_DESTINATION_RE.search(address):
        return "must not contain spaces, commas or ampersands"

    if require_e164 and not address.startswith("+"):
        return "must include a country code (start with '+')"

    return None
