"""Resolve effective config by merging per-workflow model overrides onto global config."""

from __future__ import annotations

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.registry import (
    REGISTRY,
    ServiceType,
)

# Maps override key → (UserConfiguration field, ServiceType for registry lookup)
_SECTION_MAP: dict[str, ServiceType] = {
    "llm": ServiceType.LLM,
    "tts": ServiceType.TTS,
    "stt": ServiceType.STT,
    "realtime": ServiceType.REALTIME,
}


def _build_section_from_override(service_type: ServiceType, override: dict):
    """Construct a typed config object from a raw override dict using the registry."""
    provider = override.get("provider")
    if not provider:
        return None
    registry = REGISTRY.get(service_type, {})
    config_cls = registry.get(provider)
    if config_cls is None:
        return None
    return config_cls(**override)


def resolve_effective_config(
    user_config: UserConfiguration,
    model_overrides: dict | None,
) -> UserConfiguration:
    """Deep-merge workflow model_overrides onto global user config.

    - If model_overrides is None or empty, returns a copy of user_config unchanged.
    - For each section (llm, tts, stt, realtime), if the override contains that key:
      - If the global section is None, construct a new config from the override.
      - If the provider changes, construct a new config from the override.
      - Otherwise, merge override fields onto the existing config (model_copy).
    - is_realtime is a simple boolean override.
    - Sections not in the override are inherited from global unchanged.
    - The original user_config is never mutated.
    """
    if not model_overrides:
        return user_config.model_copy(deep=True)

    effective = user_config.model_copy(deep=True)

    # Handle is_realtime boolean
    if "is_realtime" in model_overrides:
        effective.is_realtime = model_overrides["is_realtime"]

    # Handle service sections
    for section_key, service_type in _SECTION_MAP.items():
        if section_key not in model_overrides:
            continue

        override = model_overrides[section_key]
        base = getattr(effective, section_key)

        if base is None:
            # No global config for this section — build from override
            setattr(
                effective,
                section_key,
                _build_section_from_override(service_type, override),
            )
        elif "provider" in override and override["provider"] != base.provider:
            # Provider changed — must construct new typed object
            setattr(
                effective,
                section_key,
                _build_section_from_override(service_type, override),
            )
        else:
            # Same provider — merge fields onto existing config
            merged = base.model_copy(update=override)
            setattr(effective, section_key, merged)

    return effective
