"""Prepare a run once, then authorize and execute exactly its saved model setup."""

from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException
from pydantic import ValidationError

from api.db import db_client
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.pipecat.pre_call_fetch import (
    PreCallFetchConfigurationError,
    PreCallFetchResult,
    execute_pre_call_fetch_result,
)
from api.services.workflow.initial_context import merge_external_initial_context

WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY = "model_configuration_override"
PREPARATION_WAIT_SECONDS = 20


def has_prepared_model_configuration(workflow_run) -> bool:
    snapshot = getattr(workflow_run, "model_configuration_snapshot", None)
    return isinstance(snapshot, dict) and snapshot.get("preparation_state") == "ready"


def merge_run_start_context(workflow_run, supplied_context: dict | None) -> dict:
    """Saved fetch results win over a stale WebRTC/start request's variables."""
    saved = dict(workflow_run.initial_context or {})
    supplied = merge_external_initial_context({}, supplied_context)
    if has_prepared_model_configuration(workflow_run):
        return {**supplied, **saved}
    return {**saved, **supplied}


async def get_effective_ai_model_configuration_for_run(
    *, organization_id: int, workflow_run, workflow_configurations: dict | None = None
) -> EffectiveAIModelConfiguration:
    """Use saved model settings with current credentials; retain legacy readers."""
    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_workflow,
    )
    from api.services.configuration.model_connections import (
        hydrate_model_configuration_snapshot,
    )

    snapshot = getattr(workflow_run, "model_configuration_snapshot", None)
    if isinstance(snapshot, dict) and snapshot:
        if snapshot.get("preparation_state") != "ready":
            raise HTTPException(
                status_code=409, detail="Run model configuration is not ready."
            )
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


async def _fetch_for_run(workflow_run, organization_id: int) -> PreCallFetchResult:
    from api.services.workflow.dto import ReactFlowDTO
    from api.services.workflow.workflow_graph import WorkflowGraph

    definition = getattr(workflow_run, "definition", None)
    if definition is None:
        raise HTTPException(
            status_code=409, detail="Run is missing its workflow definition."
        )
    graph = WorkflowGraph(
        ReactFlowDTO.model_validate(definition.workflow_json),
        skip_instance_constraints_for={"trigger"},
    )
    start = graph.nodes.get(graph.start_node_id)
    context = dict(workflow_run.initial_context or {})
    direction = getattr(workflow_run, "call_type", None)
    direction = getattr(direction, "value", direction) or context.get("direction")
    if getattr(workflow_run, "mode", None) == "textchat":
        direction = None
    if (
        not start
        or not start.pre_call_fetch_url
        or not start.should_run_pre_call_fetch(direction)
    ):
        return PreCallFetchResult(outcome="skipped")
    return await execute_pre_call_fetch_result(
        url=start.pre_call_fetch_url,
        credential_uuid=start.pre_call_fetch_credential_uuid,
        call_context_vars=context,
        workflow_id=workflow_run.workflow_id,
        workflow_run_id=workflow_run.id,
        organization_id=organization_id,
    )


async def validate_workflow_model_compatibility(
    organization_id: int, effective: EffectiveAIModelConfiguration, definition
) -> None:
    """Check workflow consumers against the final, locally resolved setup."""
    from api.services.configuration.model_connections import (
        validate_embedding_compatibility,
    )
    from api.services.managed_model_services import get_dograh_service_api_key

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


async def prepare_run_model_configuration(
    *, organization_id: int, workflow_run
) -> EffectiveAIModelConfiguration | None:
    """Resolve/fetch once for V3 runs; None leaves legacy startup unchanged.

    The database lease serializes concurrent preparations across workers. A
    crashed owner's expired lease can be retried; the stable idempotency key
    lets the hook deduplicate that unavoidable HTTP delivery ambiguity.
    """
    from api.services.configuration.model_connections import (
        get_default_model_configuration,
        hydrate_model_configuration_snapshot,
        resolve_model_configuration,
    )

    configurations = (
        getattr(
            getattr(workflow_run, "definition", None), "workflow_configurations", None
        )
        or {}
    )
    api_override = getattr(workflow_run, "model_configuration_overrides", None)
    snapshot = getattr(workflow_run, "model_configuration_snapshot", None)
    if not snapshot and await get_default_model_configuration(organization_id) is None:
        if not api_override and not configurations.get(
            WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY
        ):
            return None
        # API-key authentication does not necessarily run signup bootstrap.
        # Import the existing organization setup before applying sparse API
        # patches; this helper does not mint or validate provider credentials.
        from api.services.configuration.model_configuration_migration import (
            ensure_organization_model_catalog,
        )

        await ensure_organization_model_catalog(organization_id)

    deadline = time.monotonic() + PREPARATION_WAIT_SECONDS
    while True:
        if isinstance(snapshot, dict) and snapshot.get("preparation_state") == "ready":
            return await hydrate_model_configuration_snapshot(organization_id, snapshot)
        if isinstance(snapshot, dict) and snapshot.get("preparation_state") == "failed":
            raise HTTPException(
                status_code=snapshot.get("error_status", 422),
                detail=snapshot.get(
                    "error_message", "Invalid run model configuration."
                ),
            )
        owner = await db_client.claim_run_model_preparation(
            workflow_run.id, organization_id
        )
        if owner is not None:
            break
        if time.monotonic() >= deadline:
            raise HTTPException(
                status_code=409,
                detail="Run configuration is being prepared. Retry shortly.",
            )
        await asyncio.sleep(0.1)
        workflow_run = await db_client.get_workflow_run(
            workflow_run.id, organization_id=organization_id
        )
        if workflow_run is None:
            raise HTTPException(status_code=404, detail="Workflow run not found.")
        snapshot = workflow_run.model_configuration_snapshot

    try:
        fetched = await _fetch_for_run(workflow_run, organization_id)
        workflow_override = await get_workflow_model_override(
            organization_id, configurations
        )
        resolved = await resolve_model_configuration(
            organization_id,
            workflow_override=workflow_override,
            api_override=api_override,
            pre_call_override=fetched.model_overrides,
        )
        await validate_workflow_model_compatibility(
            organization_id, resolved.effective, workflow_run.definition
        )
        snapshot = {
            **resolved.snapshot,
            "preparation_state": "ready",
            "pre_call_fetch_outcome": fetched.outcome,
        }
        saved = await db_client.finish_run_model_preparation(
            workflow_run.id,
            organization_id,
            owner,
            snapshot=snapshot,
            initial_context_patch=merge_external_initial_context(
                {}, fetched.initial_context
            ),
        )
        if not saved:
            raise HTTPException(
                status_code=409, detail="Run preparation changed. Retry shortly."
            )
        workflow_run.model_configuration_snapshot = snapshot
        workflow_run.initial_context = merge_external_initial_context(
            workflow_run.initial_context, fetched.initial_context
        )
        return resolved.effective
    except (HTTPException, ValidationError, ValueError) as exc:
        # ValidationError text contains raw model input. Never persist or log it.
        status = exc.status_code if isinstance(exc, HTTPException) else 422
        message = (
            exc.detail
            if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
            else "Invalid run model configuration. Review the selected connections and overrides."
        )
        if isinstance(exc, PreCallFetchConfigurationError):
            message = "Pre-call model_overrides must be an object."
        await db_client.finish_run_model_preparation(
            workflow_run.id,
            organization_id,
            owner,
            snapshot={
                "preparation_state": "failed",
                "error_status": status,
                "error_message": message,
            },
        )
        raise HTTPException(status_code=status, detail=message) from exc
