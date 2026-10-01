"""Cloudonix keeps the SIP Call-ID off the TwiML-compatible fields (``CallSid`` and
``Session`` are both its session token) but exposes it on the session object, in
``callIds``, ``profile.callId`` and a ``CID`` header. Verified against live calls.
"""

from __future__ import annotations

from typing import Any

from ._common import (
    SipMetadata,
    as_dict,
    call_id_from_headers,
    exportable_headers,
    first_str,
)

# Where Cloudonix puts forwarded SIP headers, by call origin: ``trunk-sip-headers``
# for calls arriving at the border or over a trunk, ``subscriber-sip-headers`` for
# calls placed by a domain subscriber.
_SIP_HEADER_PROFILE_FIELDS: tuple[str, ...] = (
    "trunk-sip-headers",
    "subscriber-sip-headers",
)


def extract(raw_webhook_data: dict[str, Any]) -> SipMetadata:
    session_data = raw_webhook_data.get("SessionData")
    if not isinstance(session_data, dict):
        return None, None
    profile = as_dict(session_data.get("profile"))

    headers: dict[str, str] = {}
    for field in _SIP_HEADER_PROFILE_FIELDS:
        for name, value in exportable_headers(
            as_dict(profile.get(field)).items()
        ).items():
            headers.setdefault(name, value)

    sip_call_id = (
        first_str(session_data.get("callIds"))
        or first_str(profile.get("callId"))
        or call_id_from_headers(headers)
    )
    # Surviving correlation headers are reported even without an id, so an
    # unlinked call stays diagnosable in Tuner.
    return sip_call_id, headers or None
