from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.constants import BACKEND_API_ENDPOINT, TUNER_BASE_URL
from api.services.integrations.base import IntegrationCompletionContext

from .client import TunerDeliveryConfig, post_call
from .collector import TUNER_RECORDING_PLACEHOLDER
from .node import TunerNodeData


def _build_recording_url(
    context: IntegrationCompletionContext,
) -> str | None:
    workflow_run = context.workflow_run
    if context.public_token:
        base_url = f"{BACKEND_API_ENDPOINT}/api/v1/public/download/workflow/{context.public_token}"
        return f"{base_url}/recording" if workflow_run.recording_url else None
    return workflow_run.recording_url


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
                "exported_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            logger.error(f"Tuner export failed for node '{tuner_data.name}': {exc}")
            results[f"tuner_{node_id}"] = {"error": str(exc)}

    return results
