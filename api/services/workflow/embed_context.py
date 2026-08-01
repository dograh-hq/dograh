"""Sanitization for visitor context supplied by an embedded widget.

Embed context reaches us from a third-party page via the widget script tag's
`data-dograh-context` JSON attribute, so it is untrusted in shape and size even
though the site owner holds the token.

Accepted variables land in the run's ``initial_context`` and are addressable from
prompts as ``{{initial_context.<name>}}`` (see ``api.utils.template_renderer``).
Bad entries are dropped and logged rather than rejected: a mistyped marketing tag
must not stop a visitor's conversation from starting.
"""

import json
import re
from typing import Any

from loguru import logger

from api.services.workflow.initial_context import RESERVED_INITIAL_CONTEXT_KEYS

MAX_CONTEXT_VARIABLES = 50
MAX_KEY_LENGTH = 64
MAX_VALUE_LENGTH = 2000
MAX_TOTAL_BYTES = 8192

# Dots select nested values, while whitespace, pipes, and braces delimit parts
# of a template expression. Accepting them in a top-level name would store a
# value that ``{{initial_context.<name>}}`` cannot address unambiguously.
_TEMPLATE_NAME_PATTERN = re.compile(r"^[^.\s|{}]+$")

_INVALID = object()


def _clean_value(value: Any) -> Any:
    """Return the storable form of *value*, or ``_INVALID`` if unusable."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_VALUE_LENGTH]
    if isinstance(value, (dict, list)):
        try:
            encoded = json.dumps(value)
        except (TypeError, ValueError):
            return _INVALID
        return value if len(encoded) <= MAX_VALUE_LENGTH else _INVALID
    return _INVALID


def sanitize_embed_context_variables(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Cap and clean widget-supplied context before it becomes initial_context."""
    if not isinstance(raw, dict) or not raw:
        return {}

    sanitized: dict[str, Any] = {}
    dropped: list[str] = []
    total_bytes = 0

    for key, value in raw.items():
        name = key.strip() if isinstance(key, str) else ""
        if (
            not name
            or len(name) > MAX_KEY_LENGTH
            or _TEMPLATE_NAME_PATTERN.fullmatch(name) is None
            or name in RESERVED_INITIAL_CONTEXT_KEYS
        ):
            dropped.append(str(key))
            continue

        if len(sanitized) >= MAX_CONTEXT_VARIABLES:
            dropped.append(f"{name} (over {MAX_CONTEXT_VARIABLES} variables)")
            continue

        cleaned = _clean_value(value)
        if cleaned is _INVALID:
            dropped.append(f"{name} (unsupported or oversized value)")
            continue

        size = len(name) + len(json.dumps(cleaned, default=str))
        if total_bytes + size > MAX_TOTAL_BYTES:
            dropped.append(f"{name} (over {MAX_TOTAL_BYTES} bytes total)")
            continue

        total_bytes += size
        sanitized[name] = cleaned

    if dropped:
        logger.warning(f"Dropped embed context variables: {', '.join(dropped)}")

    return sanitized
