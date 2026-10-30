from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.constants import BACKEND_API_ENDPOINT, TUNER_BASE_URL
from api.services.integrations.base import IntegrationCompletionContext

from .client import TunerDeliveryConfig, post_call
from .collector import TUNER_RECORDING_PLACEHOLDER
from .cost import compute_call_cost_cents
from .node import TunerNodeData


def _build_recording_url(
    context: IntegrationCompletionContext,
) -> str | None:
    workflow_run = context.workflow_run
    if context.public_token:
        base_url = f"{BACKEND_API_ENDPOINT}/api/v1/public/download/workflow/{context.public_token}"
        return f"{base_url}/recording" if workflow_run.recording_url else None
    return workflow_run.recording_url


def _apply_call_metadata(
    payload: dict[str, Any],
    tuner_data: TunerNodeData,
    workflow_run: Any,
) -> None:
    """Add the user-listed call variables to ``general_meta_data_raw``.

    Looks each key up in ``initial_context`` first, then in
    ``gathered_context["extracted_variables"]``. Values pass through as-is and
    keys already set by the SDK are never overwritten.
    """
    if not tuner_data.metadata_enabled or not tuner_data.metadata_keys:
        return

    initial_context = getattr(workflow_run, "initial_context", None) or {}
    gathered_context = getattr(workflow_run, "gathered_context", None) or {}
    extracted_variables = gathered_context.get("extracted_variables") or {}

    metadata = payload.setdefault("general_meta_data_raw", {})
    not_found: list[str] = []
    for key in tuner_data.metadata_keys:
        if key in initial_context:
            value = initial_context[key]
        elif key in extracted_variables:
            value = extracted_variables[key]
        else:
            not_found.append(key)
            continue

        if key in metadata:
            logger.warning(
                f"Tuner metadata key '{key}' already exists in the payload, not overwriting"
            )
            continue
        metadata[key] = value

    if not_found:
        logger.info(
            f"Tuner metadata keys not found in call context: {', '.join(not_found)}"
        )


async def run_completion(
    nodes: list[dict[str, Any]],
    context: IntegrationCompletionContext,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    payload_snapshot = (context.workflow_run.logs or {}).get("tuner_payload")
    recording_url = _build_recording_url(context) or TUNER_RECORDING_PLACEHOLDER

    for node in nodes:
        node_id = node.get("id", "unknown")
        try:
            tuner_data = TunerNodeData.model_validate(node.get("data", {}))
        except Exception as exc:
            logger.warning(f"Tuner node #{node_id} failed validation, skipping: {exc}")
            results[f"tuner_{node_id}"] = {"error": "validation_failed"}
            continue

        if not tuner_data.tuner_enabled:
            logger.debug(f"Tuner node '{tuner_data.name}' is disabled, skipping")
            continue

        if not payload_snapshot:
            logger.warning(
                f"Tuner payload snapshot missing for node '{tuner_data.name}' (#{node_id})"
            )
            results[f"tuner_{node_id}"] = {"error": "missing_payload_snapshot"}
            continue

        payload = copy.deepcopy(payload_snapshot)
        payload["recording_url"] = recording_url

        call_cost = compute_call_cost_cents(
            tuner_data,
            context.workflow_run.usage_info,
            transcript_segments=payload.get("transcript_with_tool_calls"),
        )
        if call_cost is not None:
            payload["call_cost"] = call_cost

        _apply_call_metadata(payload, tuner_data, context.workflow_run)

        try:
            config = TunerDeliveryConfig(
                base_url=TUNER_BASE_URL,
                api_key=tuner_data.tuner_api_key or "",
                workspace_id=tuner_data.tuner_workspace_id or 0,
                agent_id=tuner_data.tuner_agent_id or "",
            )
            delivery = await post_call(config, payload)
            results[f"tuner_{node_id}"] = {
                **delivery,
                "workspace_id": tuner_data.tuner_workspace_id,
                "agent_id": tuner_data.tuner_agent_id,
                **({"call_cost": call_cost} if call_cost is not None else {}),
                "exported_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            logger.error(f"Tuner export failed for node '{tuner_data.name}': {exc}")
            results[f"tuner_{node_id}"] = {"error": str(exc)}

    return results
