"""Recover the identifier Tuner links a reported call by, one module per provider.

Tuner matches a call Dograh reports back to the simulation that placed it by the
caller's SIP Call-ID. Each provider exposes it differently in its inbound webhook,
which the inbound dispatcher already persists on the workflow run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from api.enums import WorkflowRunMode

from . import cloudonix, twilio
from ._common import SipMetadata

_EXTRACTORS: dict[str, Callable[[dict[str, Any]], SipMetadata]] = {
    WorkflowRunMode.CLOUDONIX.value: cloudonix.extract,
    WorkflowRunMode.TWILIO.value: twilio.extract,
}


def extract_inbound_sip_metadata(workflow_run: Any) -> SipMetadata:
    """Return ``(sip_call_id, correlation_headers)`` for an inbound call.

    Inbound runs take the provider name as their mode. Any other mode, or a run
    with no stored webhook, yields ``(None, None)`` and delivery is unchanged.
    """
    extractor = _EXTRACTORS.get(getattr(workflow_run, "mode", None) or "")
    if extractor is None:
        return None, None
    logs = getattr(workflow_run, "logs", None) or {}
    raw_webhook_data = (logs.get("inbound_webhook") or {}).get("raw_webhook_data")
    if not isinstance(raw_webhook_data, dict):
        return None, None
    return extractor(raw_webhook_data)


__all__ = ["SipMetadata", "extract_inbound_sip_metadata"]
