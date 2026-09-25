"""Per-call model overrides that select saved provider profiles.

A call request may carry ``model_overrides`` such as::

    {"llm": {"profile": "openai-fast"}, "tts": {"profile": "eleven-eu", "voice": "Rachel"}}

The request is validated when the call is triggered and only the profile names
and non-secret field tweaks are stored on the run (under
:data:`RUN_MODEL_OVERRIDES_CONTEXT_KEY` in ``initial_context``). Credentials are read
from the organization's saved profiles when the run starts, so no secret is
ever sent in, or stored with, a call request.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.model_provider_profiles import (
    PROFILE_SERVICES,
    CallModelOverrides,
    parse_profile_config,
)
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_PROFILE_SELECTION_KEY,
    get_effective_ai_model_configuration_for_workflow,
)
from api.services.configuration.masking import SERVICE_SECRET_FIELDS
from api.services.configuration.profile_selection import (
    CallOverrideError,
    expand_profile_section,
    apply_profile_selection,
)
from api.services.configuration.provider_profiles import (
    ProfileNotFoundError,
    get_profile,
)
from api.services.configuration.registry import REGISTRY, ServiceType
from api.services.workflow.initial_context import RUN_MODEL_OVERRIDES_CONTEXT_KEY

# Re-exported: callers and tests import the historical names from here.
apply_call_model_overrides = apply_profile_selection

# Fields a caller may never override per call: credentials, the provider
# itself, and endpoint URLs (validated only when a profile is saved).
_FORBIDDEN_FIELDS = frozenset(SERVICE_SECRET_FIELDS) | {
    "provider",
    "base_url",
    "endpoint",
}


def _check_field_names(service: str, section: dict[str, Any], provider: str) -> None:
    config_cls = REGISTRY[ServiceType[service.upper()]][provider]
    fields = {key for key in section if key != "profile"}

    forbidden = sorted(fields & _FORBIDDEN_FIELDS)
    if forbidden:
        raise CallOverrideError(
            f"{service}: {', '.join(forbidden)} cannot be overridden per call; "
            "save a separate profile instead"
        )
    unknown = sorted(fields - set(config_cls.model_fields))
    if unknown:
        raise CallOverrideError(
            f"{service}: unknown field(s) for provider '{provider}': "
            f"{', '.join(unknown)}"
        )


async def validate_call_overrides(
    organization_id: int, overrides: CallModelOverrides
) -> dict[str, Any]:
    """Check ``overrides`` against the org's saved profiles.

    Returns the secret-free dict to store on the run. Raises
    :class:`CallOverrideError` for unknown profiles, forbidden or unknown
    fields, or values of the wrong type.
    """
    stored: dict[str, Any] = {}
    for service in PROFILE_SERVICES:
        override = getattr(overrides, service)
        if override is None:
            continue
        section = override.model_dump(exclude_none=True)
        try:
            profile = await get_profile(organization_id, service, section["profile"])
        except ProfileNotFoundError as exc:
            raise CallOverrideError(
                f"No saved {service} profile named '{section['profile']}'"
            ) from exc
        _check_field_names(service, section, profile.config["provider"])
        try:
            parse_profile_config(
                service,
                await expand_profile_section(organization_id, service, section),
            )
        except (ValueError, ValidationError) as exc:
            raise CallOverrideError(f"{service}: {_first_error(exc)}") from exc
        stored[service] = section

    if overrides.is_realtime is not None:
        stored["is_realtime"] = overrides.is_realtime
    return stored


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors(include_url=False, include_context=False)[0]
        location = ".".join(str(part) for part in error["loc"])
        return f"{location}: {error['msg']}" if location else error["msg"]
    return str(exc.args[0]) if exc.args else str(exc)


def missing_services(effective: EffectiveAIModelConfiguration) -> list[str]:
    """Services the run would need but the effective config lacks."""
    required = ("realtime", "llm") if effective.is_realtime else ("llm", "tts", "stt")
    return [name for name in required if getattr(effective, name) is None]


async def apply_run_model_overrides(
    base: EffectiveAIModelConfiguration,
    organization_id: int | None,
    run_initial_context: dict | None,
) -> EffectiveAIModelConfiguration:
    """Apply the per-call overrides stored on a run (if any) on top of ``base``."""
    stored = (run_initial_context or {}).get(RUN_MODEL_OVERRIDES_CONTEXT_KEY)
    if not stored or organization_id is None:
        return base
    try:
        return await apply_call_model_overrides(base, organization_id, stored)
    except (CallOverrideError, ValueError, ValidationError) as exc:
        # A profile deleted or edited after the call was triggered. Failing is
        # safer than silently running with a different provider than requested.
        raise CallOverrideError(
            f"Per-call model overrides can no longer be applied: {exc}"
        ) from exc


async def get_effective_ai_model_configuration_for_run(
    *,
    organization_id: int | None,
    workflow_configurations: dict | None,
    run_initial_context: dict | None,
) -> EffectiveAIModelConfiguration:
    """The model configuration a specific run uses.

    The workflow's effective configuration with the run's per-call overrides
    (if any) applied on top.
    """
    base = await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id,
        workflow_configurations=workflow_configurations,
    )
    return await apply_run_model_overrides(base, organization_id, run_initial_context)


async def validate_workflow_profile_selection(
    organization_id: int, workflow_configurations: dict
) -> dict[str, Any] | None:
    """Validate the saved-provider selection inside ``workflow_configurations``.

    Returns the normalised, credential-free selection to store, or ``None``
    when nothing is selected. Raises :class:`CallOverrideError` for unknown
    profiles, forbidden or unknown fields, or a selection that leaves the
    workflow without a runnable model configuration.
    """
    raw = workflow_configurations.get(WORKFLOW_MODEL_PROFILE_SELECTION_KEY)
    if not raw:
        return None
    try:
        overrides = CallModelOverrides.model_validate(raw)
    except ValidationError as exc:
        raise CallOverrideError(_first_error(exc)) from exc

    stored = await validate_call_overrides(organization_id, overrides)
    if not stored:
        return None

    effective = await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id,
        workflow_configurations={
            **workflow_configurations,
            WORKFLOW_MODEL_PROFILE_SELECTION_KEY: stored,
        },
    )
    missing = missing_services(effective)
    if missing:
        raise CallOverrideError(
            "The selected saved providers leave the workflow without a "
            f"{', '.join(missing)} configuration; select or configure one"
        )
    return stored
