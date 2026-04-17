"""Client-side validation of node data against a fetched spec.

Intentionally lightweight: we catch the hallucinations that matter (typo'd
field names, missing required fields, obvious scalar-type mismatches) and
leave rigorous coercion to the backend's Pydantic validators, which run at
save time. The tradeoff: the SDK fails fast on mistakes an LLM makes, and
the backend remains the single authority on wire-format correctness.
"""

from __future__ import annotations

from typing import Any

from .errors import ValidationError

# Map PropertyType → primitive Python type(s) we check at call site.
# `None` means "skip scalar-type check" (compound types, refs, JSON, etc.).
_SCALAR_TYPES: dict[str, tuple[type, ...] | None] = {
    "string": (str,),
    "number": (int, float),
    "boolean": (bool,),
    "options": None,  # value-in-options handled separately
    "multi_options": None,
    "fixed_collection": (list,),
    "json": None,  # any JSON-serializable
    "tool_refs": (list,),
    "document_refs": (list,),
    "recording_ref": (str,),
    "credential_ref": (str,),
    "mention_textarea": (str,),
    "url": (str,),
}


def _with_hint(prop: dict[str, Any], message: str) -> str:
    """Append `prop.llm_hint` to an error message when set.

    Surfacing the hint inside validation errors lets an LLM author
    self-correct on retry — it sees the catalog reference or value-shape
    guidance inline with the failure.
    """
    hint = prop.get("llm_hint")
    if hint:
        return f"{message}\n  Hint: {hint}"
    return message


def _check_scalar(prop: dict[str, Any], value: Any) -> None:
    # None is always allowed (missing value handled by required check).
    if value is None:
        return
    allowed = _SCALAR_TYPES.get(prop["type"])
    if allowed is None:
        return
    # Booleans ARE ints in Python, so exclude accidentally-matching bools.
    if bool in allowed and not (int in allowed or float in allowed):
        if not isinstance(value, bool):
            raise ValidationError(
                _with_hint(
                    prop,
                    f"{prop['name']}: expected boolean, got {type(value).__name__}",
                )
            )
        return
    if not isinstance(value, allowed):
        raise ValidationError(
            _with_hint(
                prop,
                f"{prop['name']}: expected {prop['type']}, "
                f"got {type(value).__name__}",
            )
        )


def _check_options(prop: dict[str, Any], value: Any) -> None:
    if value is None:
        return
    allowed = {o["value"] for o in prop.get("options") or []}
    if not allowed:
        return
    if prop["type"] == "multi_options":
        if not isinstance(value, list):
            raise ValidationError(
                _with_hint(
                    prop,
                    f"{prop['name']}: expected list, got {type(value).__name__}",
                )
            )
        bad = [v for v in value if v not in allowed]
        if bad:
            raise ValidationError(
                _with_hint(
                    prop,
                    f"{prop['name']}: values {bad} not in allowed {sorted(allowed)}",
                )
            )
    else:  # 'options'
        if value not in allowed:
            raise ValidationError(
                _with_hint(
                    prop,
                    f"{prop['name']}: {value!r} not in allowed {sorted(allowed)}",
                )
            )


def validate_node_data(
    spec: dict[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Validate LLM-supplied kwargs against the node spec. Returns the data
    dict to embed in the wire format, with defaults applied.

    Raises:
        ValidationError if kwargs contain unknown fields, omit required
        fields, or carry obvious type mismatches.
    """
    declared = {p["name"]: p for p in spec["properties"]}

    # Unknown field names — the most common LLM hallucination.
    unknown = set(kwargs) - set(declared)
    if unknown:
        raise ValidationError(
            f"{spec['name']}: unknown field(s) {sorted(unknown)}. "
            f"Allowed: {sorted(declared)}"
        )

    # Per-property validation
    data: dict[str, Any] = {}
    for name, prop in declared.items():
        if name in kwargs:
            value = kwargs[name]
        elif prop.get("default") is not None:
            value = prop["default"]
        else:
            value = None

        # Scalar / collection shape
        if prop["type"] in ("options", "multi_options"):
            _check_options(prop, value)
        else:
            _check_scalar(prop, value)

        # Nested fixed_collection rows — validate each row as a sub-spec.
        if prop["type"] == "fixed_collection" and isinstance(value, list):
            sub_spec = {"name": f"{spec['name']}.{name}", "properties": prop.get("properties") or []}
            data[name] = [validate_node_data(sub_spec, row) for row in value]
            continue

        if value is not None:
            data[name] = value

    # Required check — must be set AND non-empty for strings.
    for name, prop in declared.items():
        if not prop.get("required"):
            continue
        val = data.get(name)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            raise ValidationError(
                _with_hint(
                    prop,
                    f"{spec['name']}: required field missing: {name}",
                )
            )

    return data
