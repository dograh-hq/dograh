"""Shared value normalization for provider-owned SIP webhook parsers."""

from collections.abc import Iterable
from typing import Any


def first_sip_string(value: Any) -> str | None:
    """Read a non-empty string, including providers that wrap values in lists."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, str) and item), None)
    return None


def normalize_sip_headers(pairs: Iterable[tuple[Any, Any]]) -> dict[str, str]:
    """Keep the first non-empty value per header, comparing names case-insensitively."""
    headers: dict[str, str] = {}
    seen: set[str] = set()
    for name, value in pairs:
        if not isinstance(name, str) or name.lower() in seen or value is None:
            continue
        text = first_sip_string(value) if isinstance(value, (str, list)) else str(value)
        if text:
            headers[name] = text
            seen.add(name.lower())
    return headers
