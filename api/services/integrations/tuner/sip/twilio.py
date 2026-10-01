"""Twilio SIP Domain webhooks carry the caller's SIP Call-ID as ``SipCallId``."""

from __future__ import annotations

from typing import Any

from ._common import SipMetadata, first_str


def extract(raw_webhook_data: dict[str, Any]) -> SipMetadata:
    return first_str(raw_webhook_data.get("SipCallId")), None
