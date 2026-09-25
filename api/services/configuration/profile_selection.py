"""Apply saved provider profiles to an effective model configuration.

Shared by per-call ``model_overrides`` and a workflow's saved-provider
selection. Kept free of imports from ``ai_model_configuration`` so that module
can call it without an import cycle.
"""

from __future__ import annotations

from typing import Any

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.model_provider_profiles import PROFILE_SERVICES, parse_profile_config
from api.services.configuration.provider_profiles import (
    ProfileNotFoundError,
    get_profile,
)


class CallOverrideError(ValueError):
    """The requested overrides cannot be applied (maps to HTTP 422)."""


async def expand_profile_section(
    organization_id: int, service: str, section: dict[str, Any]
) -> dict[str, Any]:
    name = section["profile"]
    try:
        profile = await get_profile(organization_id, service, name)
    except ProfileNotFoundError as exc:
        raise CallOverrideError(f"No saved {service} profile named '{name}'") from exc
    fields = {key: value for key, value in section.items() if key != "profile"}
    return {**profile.config, **fields}


async def apply_profile_selection(
    base: EffectiveAIModelConfiguration,
    organization_id: int,
    stored: dict[str, Any] | None,
) -> EffectiveAIModelConfiguration:
    """Return ``base`` with a profile selection applied.

    ``stored`` maps services to ``{"profile": name, ...tweaks}`` and may carry
    ``is_realtime``.

    Each overridden service is *replaced* by the profile's config (with the
    call's field tweaks), never merged into the default: merging could pair a
    profile's API key with a different endpoint from the default config.
    """
    if not stored:
        return base

    effective = base.model_copy(deep=True)
    for service in PROFILE_SERVICES:
        section = stored.get(service)
        if not section:
            continue
        config = await expand_profile_section(organization_id, service, section)
        setattr(effective, service, parse_profile_config(service, config))

    if stored.get("is_realtime") is not None:
        effective.is_realtime = bool(stored["is_realtime"])
    elif stored.get("realtime"):
        effective.is_realtime = True
    return effective
