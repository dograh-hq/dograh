"""Twilio SIP Domain webhooks carry the caller's SIP Call-ID as ``SipCallId`` and
each custom INVITE header as ``SipHeader_<name>``.
"""

from __future__ import annotations

from typing import Any

from ._common import SipMetadata, exportable_headers, first_str

_SIP_HEADER_PREFIX = "SipHeader_"


def extract(raw_webhook_data: dict[str, Any]) -> SipMetadata:
    headers = exportable_headers(
        (key.removeprefix(_SIP_HEADER_PREFIX), value)
        for key, value in raw_webhook_data.items()
        if isinstance(key, str) and key.startswith(_SIP_HEADER_PREFIX)
    )
    # Correlation headers are reported for diagnosis only; the Call-ID links the call.
    return first_str(raw_webhook_data.get("SipCallId")), headers or None
