"""Select normalized call metadata for Tuner's simulation correlation."""

from typing import Any

# Forwarded SIP headers can contain credentials, addresses and account ids.
# Only correlation identifiers belong in the report sent to Tuner.
_EXPORTABLE_SIP_HEADER_NAMES = frozenset(
    {"cid", "call-id", "correlation-id", "x-correlation-id"}
)


def get_sip_metadata(workflow_run: Any) -> tuple[str | None, dict[str, str] | None]:
    """Read metadata captured by telephony, without interpreting provider payloads."""
    gathered_context = getattr(workflow_run, "gathered_context", None) or {}
    sip_call_id = gathered_context.get("sip_call_id")
    if not isinstance(sip_call_id, str) or not sip_call_id:
        sip_call_id = None

    logs = getattr(workflow_run, "logs", None) or {}
    headers = (logs.get("inbound_webhook") or {}).get("sip_headers")
    headers = headers if isinstance(headers, dict) else {}
    exportable = {
        name: value
        for name, value in headers.items()
        if isinstance(name, str)
        and name.lower() in _EXPORTABLE_SIP_HEADER_NAMES
        and isinstance(value, str)
        and value
    }
    return sip_call_id, exportable or None
