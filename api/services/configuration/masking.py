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


# ---------------------------------------------------------------------------
# High-level helpers for UserConfiguration objects
# ---------------------------------------------------------------------------


def _mask_service(service_cfg: Optional[ServiceConfig]) -> Optional[Dict[str, Any]]:
    if service_cfg is None:
        return None

    # Work on a dict copy so we don't mutate original models
    data = service_cfg.model_dump()
    if "api_key" in data and data["api_key"]:
        data["api_key"] = mask_key(data["api_key"])
    return data


def mask_user_config(config: UserConfiguration) -> Dict[str, Any]:
    """Return a JSON-serialisable dict of *config* with every api_key masked."""

    return {
        "llm": _mask_service(config.llm),
        "tts": _mask_service(config.tts),
        "stt": _mask_service(config.stt),
        "embeddings": _mask_service(config.embeddings),
        "test_phone_number": config.test_phone_number,
        "timezone": config.timezone,
    }
