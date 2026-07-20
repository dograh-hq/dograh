"""Paygent post-call completion handler.

Reads the ``paygent_snapshot`` that the runtime session stored in
``workflow_run.logs``, reconstructs the full ``PaygentCallSnapshot``, and
drives the ordered REST delivery sequence via ``client.deliver()``.

Mirrors ``tuner/completion.py`` exactly:
- validate each node with Pydantic
- skip disabled nodes
- read runtime snapshot from ``context.workflow_run.logs``
- build a ``PaygentDeliveryConfig`` per node
- call ``deliver(config, snapshot)``
- collect results keyed by ``paygent_{node_id}``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.services.integrations.base import IntegrationCompletionContext

from .client import PaygentCallSnapshot, PaygentDeliveryConfig, deliver
from .node import PaygentNodeData

_DEFAULT_BASE_URL = "https://cp-api.withpaygent.com"


def _build_snapshot(
    raw: dict[str, Any],
    *,
    workflow_run_id: int,
) -> PaygentCallSnapshot:
    """Reconstruct a ``PaygentCallSnapshot`` from the persisted log dict."""
    return PaygentCallSnapshot(
        # session_id is always the authoritative workflow_run_id; the persisted
        # snapshot value is never used to override it, preventing billing drift
        # if the log is stale or corrupted.
        session_id=str(workflow_run_id),
        agent_id=raw.get("agent_id", ""),  # filled from node config below
        customer_id=raw.get("customer_id", ""),  # filled from node config below
        is_realtime=raw.get("is_realtime", False),
        stt_provider=raw.get("stt_provider", ""),
        stt_model=raw.get("stt_model", ""),
        stt_audio_seconds=float(raw.get("stt_audio_seconds", 0.0)),
        llm_provider=raw.get("llm_provider", ""),
        llm_model=raw.get("llm_model", ""),
        llm_prompt_tokens=int(raw.get("llm_prompt_tokens", 0)),
        llm_completion_tokens=int(raw.get("llm_completion_tokens", 0)),
        llm_cached_tokens=int(raw.get("llm_cached_tokens", 0)),
        tts_provider=raw.get("tts_provider", ""),
        tts_model=raw.get("tts_model", ""),
        tts_characters=int(raw.get("tts_characters", 0)),
        sts_provider=raw.get("sts_provider", ""),
        sts_model=raw.get("sts_model", ""),
        sts_usage_metadata=raw.get("sts_usage_metadata"),
        call_disposition=raw.get("call_disposition", "completed"),
        total_duration_seconds=int(raw.get("total_duration_seconds", 0)),
    )


async def run_completion(
    nodes: list[dict[str, Any]],
    context: IntegrationCompletionContext,
) -> dict[str, Any]:
    """Post-call completion handler: deliver usage data to Paygent REST API."""
    results: dict[str, Any] = {}

    raw_snapshot: dict[str, Any] | None = (context.workflow_run.logs or {}).get(
        "paygent_snapshot"
    )

    for node in nodes:
        node_id = node.get("id", "unknown")

        # ---- Validate the node config via Pydantic -------------------------
        try:
            node_data = PaygentNodeData.model_validate(node.get("data", {}))
        except Exception:
            results[f"paygent_{node_id}"] = {"error": "validation_failed"}
            continue

        if not node_data.paygent_enabled:
            continue

        # ---- Guard: runtime snapshot must exist ----------------------------
        if not raw_snapshot:
            results[f"paygent_{node_id}"] = {"error": "missing_runtime_snapshot"}
            continue

        # ---- Build typed objects -------------------------------------------
        snapshot = _build_snapshot(
            raw_snapshot, workflow_run_id=context.workflow_run_id
        )
        # Inject node-level credentials into the snapshot
        snapshot.agent_id = (node_data.paygent_agent_id or "").strip()
        snapshot.customer_id = (node_data.paygent_customer_id or "").strip()
        snapshot.indicator = (node_data.paygent_indicator or "per-minute-call").strip()

        # Fallback to usage_info if snapshot has 0s (Pipecat metrics might be missing)
        usage_info = context.workflow_run.usage_info or {}
        try:
            # Only fallback to pipeline-level llm usage if this is NOT a realtime pipeline.
            # In realtime pipelines, the collector properly segregates STS and LLM tokens;
            # falling back here would duplicate the STS tokens into the LLM bucket.
            if (
                snapshot.llm_prompt_tokens == 0
                and snapshot.llm_completion_tokens == 0
                and not snapshot.is_realtime
            ):
                llm_providers: list[str] = []
                llm_models: list[str] = []
                for key, val in usage_info.get("llm", {}).items():
                    # Skip post-call QA analysis entries — they must not be billed
                    # as in-conversation LLM usage.
                    if key.startswith("QAAnalysis|||"):
                        continue
                    snapshot.llm_prompt_tokens += val.get("prompt_tokens", 0)
                    snapshot.llm_completion_tokens += val.get("completion_tokens", 0)
                    snapshot.llm_cached_tokens += val.get(
                        "cache_read_input_tokens", 0
                    ) + val.get("cache_creation_input_tokens", 0)
                    parts = key.split("|||")
                    if len(parts) == 2:
                        llm_providers.append(parts[0])
                        llm_models.append(parts[1])
                if not snapshot.llm_provider and llm_providers:
                    snapshot.llm_provider = ",".join(dict.fromkeys(llm_providers))
                if not snapshot.llm_model and llm_models:
                    snapshot.llm_model = ",".join(dict.fromkeys(llm_models))

            if snapshot.tts_characters == 0:
                tts_providers: list[str] = []
                tts_models: list[str] = []
                for key, val in usage_info.get("tts", {}).items():
                    snapshot.tts_characters += val
                    parts = key.split("|||")
                    if len(parts) == 2:
                        tts_providers.append(parts[0])
                        tts_models.append(parts[1])
                if not snapshot.tts_provider and tts_providers:
                    snapshot.tts_provider = ",".join(dict.fromkeys(tts_providers))
                if not snapshot.tts_model and tts_models:
                    snapshot.tts_model = ",".join(dict.fromkeys(tts_models))

            if snapshot.stt_audio_seconds == 0:
                stt_providers: list[str] = []
                stt_models: list[str] = []
                for key, val in usage_info.get("stt", {}).items():
                    snapshot.stt_audio_seconds += val
                    parts = key.split("|||")
                    if len(parts) == 2:
                        stt_providers.append(parts[0])
                        stt_models.append(parts[1])
                if not snapshot.stt_provider and stt_providers:
                    snapshot.stt_provider = ",".join(dict.fromkeys(stt_providers))
                if not snapshot.stt_model and stt_models:
                    snapshot.stt_model = ",".join(dict.fromkeys(stt_models))
                # Note: if STT audio seconds remain 0 after all fallbacks, we do NOT
                # substitute total_duration_seconds — that would overbill wall-clock time
                # (silence, hold, agent speech) as STT input.
        except Exception as exc:
            logger.warning(
                "[paygent] Failed to apply usage_info fallback for run {}: {}",
                context.workflow_run_id,
                exc,
            )

        try:
            config = PaygentDeliveryConfig(
                api_key=(node_data.paygent_api_key or "").strip(),
                agent_id=snapshot.agent_id,
                customer_id=snapshot.customer_id,
            )
        except Exception as exc:
            results[f"paygent_{node_id}"] = {"error": f"invalid_config: {exc}"}
            continue

        # ---- REST delivery -------------------------------------------------
        try:
            delivery_result = await deliver(config, snapshot)
            results[f"paygent_{node_id}"] = {
                **delivery_result,
                "agent_id": snapshot.agent_id,
                "customer_id": snapshot.customer_id,
                "exported_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            results[f"paygent_{node_id}"] = {"error": str(exc)}

    return results
