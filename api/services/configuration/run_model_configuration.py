"""Resolve a run's model configuration in two steps around the call connecting.

Before the caller is connected, authorization pins the call-scoped setup:
organization default, then the workflow definition's override, then the API
trigger's override. After the caller is connected the pre-call fetch may still
patch the services an agent visit owns, and only those, so the call pipeline
that is already running is never rebuilt.
"""

from __future__ import annotations

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError

from api.db import db_client
from api.errors.failure import (
    DograhFailure,
    ErrorSource,
    ErrorType,
    log_failure,
)
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.model_connections import PreCallModelOverride
from api.services.pipecat.pre_call_fetch import PreCallFetchResult

WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY = "model_configuration_override"


async def get_effective_ai_model_configuration_for_run(
    *, organization_id: int, workflow_run, workflow_configurations: dict | None = None
) -> EffectiveAIModelConfiguration:
    """Hydrate the run's pinned setup with current credentials.

    A run that predates pinning (historical QA, old text-chat sessions) falls
    back to resolving its definition's configuration.
    """
    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_workflow,
    )
    from api.services.configuration.model_connections import (
        hydrate_model_configuration_snapshot,
    )

    snapshot = getattr(workflow_run, "model_configuration_snapshot", None)
    if isinstance(snapshot, dict) and snapshot:
        return await hydrate_model_configuration_snapshot(organization_id, snapshot)
    if workflow_configurations is None:
        definition = getattr(workflow_run, "definition", None)
        workflow_configurations = getattr(definition, "workflow_configurations", None)
    return await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id,
        workflow_configurations=workflow_configurations,
    )


async def get_workflow_model_override(
    organization_id: int, configurations: dict
) -> dict | None:
    override = configurations.get(WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY)
    # {} explicitly inherits the catalog default. A migrated row may still
    # contain its original legacy overrides as audit data; never revive them.
    if override is not None:
        return override
    if configurations.get("model_configuration_v2_override") or configurations.get(
        "model_overrides"
    ):
        # Catalog adoption and workflow normalization can be deployed separately.
        # Keep an existing published inline setup authoritative until converted.
        from api.services.configuration.model_configuration_migration import (
            ensure_legacy_workflow_model_configuration,
        )

        spec = await ensure_legacy_workflow_model_configuration(
            organization_id, configurations
        )
        data = (
            spec.model_dump(mode="json", exclude_none=True)
            if hasattr(spec, "model_dump")
            else dict(spec)
        )
        data.pop("version", None)
        return data
    return None


async def validate_workflow_model_compatibility(
    organization_id: int, effective: EffectiveAIModelConfiguration, definition
) -> None:
    """Check workflow consumers against the final, locally resolved setup."""
    from api.services.configuration.model_connections import (
        validate_embedding_compatibility,
    )
    from api.services.managed_model_services import get_dograh_service_api_key

    if definition is None:
        # A run that predates pinned definitions has no consumers to check.
        return
    nodes = (definition.workflow_json or {}).get("nodes", [])
    documents = sorted(
        {
            uuid
            for node in nodes
            for uuid in (node.get("data", {}).get("document_uuids") or [])
        }
    )
    await validate_embedding_compatibility(organization_id, effective, documents)
    # Legacy explicit QA/voicemail settings remain readable, but must not use a
    # correlation ID authorized under an unrelated managed service key.
    key = get_dograh_service_api_key(effective)
    for node in nodes:
        data = node.get("data", {})
        if (
            data.get("qa_use_workflow_llm") is False
            and data.get("qa_provider") == "dograh"
        ):
            if not key or data.get("qa_api_key") != key:
                raise HTTPException(
                    status_code=422,
                    detail="QA must use the run's selected Dograh service key.",
                )
    voicemail = (definition.workflow_configurations or {}).get(
        "voicemail_detection"
    ) or {}
    if (
        voicemail.get("enabled")
        and voicemail.get("use_workflow_llm") is False
        and voicemail.get("provider") == "dograh"
    ):
        if not key or voicemail.get("api_key") != key:
            raise HTTPException(
                status_code=422,
                detail="Voicemail detection must use the run's selected Dograh service key.",
            )


def _configuration_failure(exc: Exception) -> HTTPException:
    # ValidationError text contains raw model input. Never persist or log it.
    status = exc.status_code if isinstance(exc, HTTPException) else 422
    message = (
        exc.detail
        if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
        else "Invalid run model configuration. Review the selected connections and overrides."
    )
    return HTTPException(status_code=status, detail=message)


def _run_inputs(workflow_run) -> tuple[dict, dict | None]:
    configurations = (
        getattr(
            getattr(workflow_run, "definition", None), "workflow_configurations", None
        )
        or {}
    )
    return configurations, getattr(workflow_run, "model_configuration_overrides", None)


async def resolve_run_model_configuration(
    *, organization_id: int, workflow_run
) -> EffectiveAIModelConfiguration:
    """Pin the call-scoped model setup before the caller is connected.

    Layers organization, workflow and API overrides, validates, and stores the
    result on the run once. Authorization runs against this setup, so the
    pre-call fetch applied later can only patch within its Dograh key.
    """
    from api.services.configuration.model_connections import (
        get_default_model_configuration,
        hydrate_model_configuration_snapshot,
        resolve_model_configuration,
    )

    snapshot = getattr(workflow_run, "model_configuration_snapshot", None)
    if isinstance(snapshot, dict) and snapshot:
        return await hydrate_model_configuration_snapshot(organization_id, snapshot)

    if await get_default_model_configuration(organization_id) is None:
        # API-key authentication does not necessarily run signup bootstrap.
        # Import the existing organization setup; this helper does not mint
        # or validate provider credentials.
        from api.services.configuration.model_configuration_migration import (
            ensure_organization_model_catalog,
        )

        await ensure_organization_model_catalog(organization_id)

    configurations, api_override = _run_inputs(workflow_run)
    try:
        resolved = await resolve_model_configuration(
            organization_id,
            workflow_override=await get_workflow_model_override(
                organization_id, configurations
            ),
            api_override=api_override,
        )
        await validate_workflow_model_compatibility(
            organization_id, resolved.effective, workflow_run.definition
        )
    except (HTTPException, ValidationError, ValueError) as exc:
        raise _configuration_failure(exc) from exc

    stored = await db_client.store_model_configuration_snapshot_if_absent(
        workflow_run.id, organization_id, resolved.snapshot
    )
    workflow_run.model_configuration_snapshot = stored
    if stored is resolved.snapshot:
        return resolved.effective
    return await hydrate_model_configuration_snapshot(organization_id, stored)


async def apply_pre_call_model_overrides(
    *,
    organization_id: int,
    workflow_run,
    run_model_configuration: EffectiveAIModelConfiguration,
    fetched: PreCallFetchResult,
) -> EffectiveAIModelConfiguration | None:
    """Layer the hook's visit-scoped overrides onto the run's pinned setup.

    Returns the final configuration, or None when the hook changed nothing or
    its overrides were rejected. A rejection is logged, never raised: the
    caller is already on the line, so the call proceeds on the setup that
    was authorized.
    """
    from api.services.configuration.model_connections import (
        resolve_model_configuration,
    )
    from api.services.managed_model_services import get_dograh_service_api_key

    if fetched.model_overrides is None:
        return None

    configurations, api_override = _run_inputs(workflow_run)
    try:
        override = PreCallModelOverride.model_validate(fetched.model_overrides)
        resolved = await resolve_model_configuration(
            organization_id,
            workflow_override=await get_workflow_model_override(
                organization_id, configurations
            ),
            api_override=api_override,
            pre_call_override=override.model_dump(mode="json", exclude_unset=True),
            preferred_dograh_key=get_dograh_service_api_key(run_model_configuration),
        )
        await validate_workflow_model_compatibility(
            organization_id, resolved.effective, workflow_run.definition
        )
    except (HTTPException, ValidationError, ValueError) as exc:
        failure = _configuration_failure(exc)
        log_failure(
            DograhFailure(
                source=ErrorSource.INTEGRATION,
                type=ErrorType.CONFIG_ERROR,
                code="pre-call-model-overrides-rejected",
                internal_message=f"Pre-call model overrides rejected: {failure.detail}",
                external_message=(
                    "The pre-call fetch returned model overrides that could not be "
                    "applied. The call continued with its configured models."
                ),
                provider="pre-call-fetch",
                error_owner="user",
                retryable=False,
            ),
            organization_id=organization_id,
            workflow_id=workflow_run.workflow_id,
            workflow_run_id=workflow_run.id,
        )
        return None

    await db_client.update_workflow_run(
        workflow_run.id, model_configuration_snapshot=resolved.snapshot
    )
    workflow_run.model_configuration_snapshot = resolved.snapshot
    effective = resolved.effective
    tts = effective.tts
    logger.info(
        f"Pre-call model overrides applied for run {workflow_run.id}: "
        f"patched={sorted(override.model_fields_set)} "
        f"llm={effective.llm.provider}/{effective.llm.model} "
        + (
            f"tts={tts.provider}/{tts.model}/voice={getattr(tts, 'voice', None)}"
            if tts is not None
            else "tts=none"
        )
    )
    return effective
