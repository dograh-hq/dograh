from __future__ import annotations

from collections.abc import Iterable
from typing import Any

SipMetadata = tuple[str | None, dict[str, str] | None]

# Header fallbacks, in preference order, for when a provider exposes no SIP Call-ID.
# ``CID`` carries the Call-ID itself. The correlation ids after it are Tuner's own
# ``X-Correlation-Id`` marker, which some providers forward with the ``X-`` stripped.
SIP_CALL_ID_HEADER_NAMES: tuple[str, ...] = (
    "cid",
    "call-id",
    "correlation-id",
    "x-correlation-id",
)

# Only correlation identifiers leave the deployment. Forwarded headers also carry
# provider authentication, source addresses and upstream account ids, which Tuner
# never correlates on.
EXPORTABLE_SIP_HEADER_NAMES: frozenset[str] = frozenset(SIP_CALL_ID_HEADER_NAMES)


def first_str(value: Any) -> str | None:
    """First non-empty string in a list, or the value itself if it is one."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                return item
    return None


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def exportable_headers(pairs: Iterable[tuple[Any, Any]]) -> dict[str, str]:
    """Keep the first non-empty value of each allowlisted correlation header."""
    headers: dict[str, str] = {}
    for name, value in pairs:
        if not isinstance(name, str) or name.lower() not in EXPORTABLE_SIP_HEADER_NAMES:
            continue
        text = _header_text(value)
        if text:
            headers.setdefault(name, text)
    return headers


def _header_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, list)):
        return first_str(value)
    return str(value)


def call_id_from_headers(headers: dict[str, str]) -> str | None:
    lowered = {name.lower(): value for name, value in headers.items()}
    return next(
        (lowered[name] for name in SIP_CALL_ID_HEADER_NAMES if lowered.get(name)),
        None,
    )
