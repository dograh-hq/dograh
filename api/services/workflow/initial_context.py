"""Shared rules for merging externally supplied workflow-run context."""

from collections.abc import Mapping
from typing import Any

from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY

# These values describe or authorize the run itself. External context may add
# prompt variables, but it must never supply or replace run-owned metadata.
RESERVED_INITIAL_CONTEXT_KEYS = frozenset(
    {"provider", "runtime_configuration", MPS_CORRELATION_ID_CONTEXT_KEY}
)


def merge_external_initial_context(
    initial_context: Mapping[str, Any] | None,
    external_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge external variables without accepting reserved run-owned keys."""
    merged = dict(initial_context or {})
    if not external_context:
        return merged

    merged.update(
        {
            key: value
            for key, value in external_context.items()
            if key not in RESERVED_INITIAL_CONTEXT_KEYS
        }
    )
    return merged
