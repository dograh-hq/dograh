"""Provider-neutral helpers for external-PBX workflow mappings."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def _read_path(context: Mapping[str, Any], path: str) -> Any:
    normalized = path.strip()
    if normalized.startswith("gathered_context."):
        normalized = normalized.removeprefix("gathered_context.")
    current: Any = context
    for part in normalized.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    if current is None and "." not in normalized:
        extracted = context.get("extracted_variables")
        if isinstance(extracted, Mapping):
            current = extracted.get(normalized)
    return current


def resolve_external_pbx_field_mappings(
    gathered_context: Mapping[str, Any] | None,
    mappings: Iterable[Mapping[str, Any]] | None,
) -> dict[str, str]:
    """Return provider field -> non-empty gathered-context value."""

    context = gathered_context or {}
    resolved: dict[str, str] = {}
    for mapping in mappings or []:
        context_path = str(mapping.get("context_path", "")).strip()
        destination_field = str(mapping.get("destination_field", "")).strip()
        if not context_path or not destination_field:
            continue
        value = _read_path(context, context_path)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            resolved[destination_field] = text
    return resolved
