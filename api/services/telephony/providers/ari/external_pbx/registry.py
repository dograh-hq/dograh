"""Registry for drop-in external-PBX adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import ExternalPBXAdapter

AdapterFactory = Callable[[dict[str, Any]], ExternalPBXAdapter]
_FACTORIES: dict[str, AdapterFactory] = {}


def register_adapter(pbx_type: str, factory: AdapterFactory) -> None:
    normalized = pbx_type.strip().lower()
    if not normalized:
        raise ValueError("External PBX type cannot be empty")
    if normalized in _FACTORIES:
        raise ValueError(f"External PBX adapter already registered: {normalized}")
    _FACTORIES[normalized] = factory


def create_adapter(config: dict[str, Any] | None) -> ExternalPBXAdapter | None:
    if not config:
        return None
    pbx_type = str(config.get("type", "")).strip().lower()
    factory = _FACTORIES.get(pbx_type)
    if factory is None:
        raise ValueError(f"Unsupported external PBX type: {pbx_type or '<empty>'}")
    return factory(config)


def registered_adapter_types() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))
