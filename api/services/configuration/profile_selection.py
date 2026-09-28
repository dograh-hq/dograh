"""Apply saved provider profiles to an effective model configuration.

Shared by per-call ``model_overrides`` and a workflow's saved-provider
selection. Kept free of imports from ``ai_model_configuration`` so that module
can call it without an import cycle.
"""

from __future__ import annotations

from typing import Any

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.model_provider_profiles import PROFILE_SERVICES, parse_profile_config
from api.services.configuration.masking import SERVICE_SECRET_FIELDS
from api.services.configuration.provider_profiles import (
    ProfileNotFoundError,
    get_profile,
)

# A per-call/per-workflow field tweak may never carry credentials, switch the
# provider, or point at a different endpoint — those require a separate saved
# profile. `call_model_overrides.validate_call_overrides` already rejects
# these before anything is stored; this module re-checks at the point that
# actually merges a tweak onto a profile's real credentials, so a future
# caller that stores an override without going through validation first
# can't smuggle a secret field into a run's resolved config.
_FORBIDDEN_TWEAK_FIELDS = frozenset(SERVICE_SECRET_FIELDS) | {
    "provider",
    "base_url",
    "endpoint",
}


class CallOverrideError(ValueError):
    """The requested overrides cannot be applied (maps to HTTP 422)."""


def looks_like_profile_selection(value: Any) -> bool:
    """Cheap, local, I/O-free shape check for a candidate selection dict.

    Used by best-effort speculative callers (a peek that should fall back to
    an unaugmented config rather than fail outright on bad input) to decide
    whether the value is even worth attempting to resolve. Returning False
    for obviously malformed input (not a dict, or a value that isn't a
    ``{"profile": ...}``-shaped dict) lets such a caller skip the real
    resolution entirely -- rather than attempt it and catch broadly, which
    would also hide a genuine transient failure (e.g. a real storage error)
    behind the same fallback and report a confusing, unrelated error instead.
    """
    if not isinstance(value, dict):
        return False
    for key, section in value.items():
        if key == "is_realtime":
            continue
        if not isinstance(section, dict) or "profile" not in section:
            return False
    return True


async def expand_profile_section(
    organization_id: int, service: str, section: dict[str, Any]
) -> dict[str, Any]:
    name = section["profile"]
    try:
        profile = await get_profile(organization_id, service, name)
    except ProfileNotFoundError as exc:
        raise CallOverrideError(f"No saved {service} profile named '{name}'") from exc
    fields = {key: value for key, value in section.items() if key != "profile"}
    forbidden = sorted(_FORBIDDEN_TWEAK_FIELDS & set(fields))
    if forbidden:
        raise CallOverrideError(
            f"{service}: {', '.join(forbidden)} cannot be overridden; "
            "save a separate profile instead"
        )
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
