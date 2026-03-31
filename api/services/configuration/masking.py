from __future__ import annotations

"""Utilities for masking API keys before they are sent to the client.

The rules are simple:
1. Only expose the last *visible* characters (default 4) of a key.
2. Incoming masked keys are considered a placeholder – if they equal the mask of
   the already-stored key, we treat them as *unchanged* and keep the real value
   in storage.
"""

from typing import Any, Dict, Optional

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.registry import ServiceConfig

VISIBLE_CHARS = 4  # number of trailing characters to reveal
MASK_CHAR = "*"
MASK_MARKER = "***"  # substring that indicates a masked key


def contains_masked_key(api_key: str | list[str] | None) -> bool:
    """Return True if *api_key* looks like a masked placeholder."""
    if api_key is None:
        return False
    keys = api_key if isinstance(api_key, list) else [api_key]
    return any(MASK_MARKER in k for k in keys)


def check_for_masked_keys(config: "UserConfiguration") -> None:
    """Raise ValueError if any service in *config* still has a masked API key."""
    for field in ("llm", "tts", "stt", "embeddings", "realtime"):
        service = getattr(config, field, None)
        if service is None:
            continue
        if contains_masked_key(service.get_all_api_keys()):
            raise ValueError(
                f"The {field} api_key appears to be masked. "
                "Please provide the actual API key, not the masked value."
            )


def mask_key(real_key: str, visible: int = VISIBLE_CHARS) -> str:
    """Return a masked representation of *real_key*.

    Example:
        >>> mask_key("sk-1234567890abcdef")
        '****************cdef'
    """
    if real_key is None:
        return ""

    if visible <= 0 or visible >= len(real_key):
        # mask entire key or nothing to mask – edge-cases
        return MASK_CHAR * len(real_key)

    masked_part = MASK_CHAR * (len(real_key) - visible)
    return f"{masked_part}{real_key[-visible:]}"


def is_mask_of(masked: str, real_key: str) -> bool:
    """Return *True* if *masked* equals the mask of *real_key* under the current rules."""
    return mask_key(real_key) == masked


def resolve_masked_api_keys(
    incoming: str | list[str], existing: str | list[str]
) -> str | list[str]:
    """Resolve masked API keys against existing real keys.

    For each incoming key, if it matches the mask of an existing key, the real
    key is restored.  New (unmasked) keys are kept as-is.  This handles adds,
    removes, reorders, and partial replacements correctly.
    """
    if isinstance(incoming, str) and isinstance(existing, str):
        return existing if is_mask_of(incoming, existing) else incoming

    existing_list = existing if isinstance(existing, list) else [existing]
    incoming_list = incoming if isinstance(incoming, list) else [incoming]

    resolved: list[str] = []
    used: set[int] = set()
    for key in incoming_list:
        matched = False
        for i, real in enumerate(existing_list):
            if i not in used and is_mask_of(key, real):
                resolved.append(real)
                used.add(i)
                matched = True
                break
        if not matched:
            resolved.append(key)
    return resolved


# ---------------------------------------------------------------------------
# High-level helpers for UserConfiguration objects
# ---------------------------------------------------------------------------


def _mask_service(service_cfg: Optional[ServiceConfig]) -> Optional[Dict[str, Any]]:
    if service_cfg is None:
        return None

    # Work on a dict copy so we don't mutate original models
    data = service_cfg.model_dump()
    if "api_key" in data and data["api_key"]:
        raw = data["api_key"]
        if isinstance(raw, list):
            data["api_key"] = [mask_key(k) for k in raw]
        else:
            data["api_key"] = mask_key(raw)
    return data


def mask_user_config(config: UserConfiguration) -> Dict[str, Any]:
    """Return a JSON-serialisable dict of *config* with every api_key masked."""

    return {
        "llm": _mask_service(config.llm),
        "tts": _mask_service(config.tts),
        "stt": _mask_service(config.stt),
        "embeddings": _mask_service(config.embeddings),
        "realtime": _mask_service(config.realtime),
        "is_realtime": config.is_realtime,
        "test_phone_number": config.test_phone_number,
        "timezone": config.timezone,
    }


# ---------------------------------------------------------------------------
# Workflow definition helpers – mask / merge QA-node API keys
# ---------------------------------------------------------------------------

_QA_API_KEY_FIELD = "qa_api_key"


def mask_workflow_definition(workflow_definition: Optional[Dict]) -> Optional[Dict]:
    """Return a *shallow copy* of *workflow_definition* with QA-node API keys masked."""
    if not workflow_definition:
        return workflow_definition

    import copy

    masked = copy.deepcopy(workflow_definition)
    for node in masked.get("nodes", []):
        if node.get("type") != "qa":
            continue
        data = node.get("data", {})
        raw_key = data.get(_QA_API_KEY_FIELD)
        if raw_key:
            data[_QA_API_KEY_FIELD] = mask_key(raw_key)
    return masked


def merge_workflow_api_keys(
    incoming_definition: Optional[Dict], existing_definition: Optional[Dict]
) -> Optional[Dict]:
    """Preserve real QA-node API keys when the incoming value is a masked placeholder.

    For each QA node in *incoming_definition*, if its ``qa_api_key`` equals
    the masked form of the corresponding node in *existing_definition*, the
    real key is restored so it is never lost.
    """
    if not incoming_definition or not existing_definition:
        return incoming_definition

    # Build lookup: node-id → data for existing QA nodes
    existing_qa: Dict[str, Dict] = {}
    for node in existing_definition.get("nodes", []):
        if node.get("type") == "qa":
            existing_qa[node["id"]] = node.get("data", {})

    for node in incoming_definition.get("nodes", []):
        if node.get("type") != "qa":
            continue
        data = node.get("data", {})
        incoming_key = data.get(_QA_API_KEY_FIELD)
        if not incoming_key:
            continue

        old_data = existing_qa.get(node["id"])
        if not old_data:
            continue

        old_key = old_data.get(_QA_API_KEY_FIELD, "")
        if old_key and is_mask_of(incoming_key, old_key):
            data[_QA_API_KEY_FIELD] = old_key

    return incoming_definition
