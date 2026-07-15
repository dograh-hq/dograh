"""Paygent runtime session.

Wires the ``PaygentCollector`` into the live pipecat pipeline exactly the way
``TunerRuntimeSession`` wires ``TunerCollector``.

Lifecycle:
  1. ``create_runtime_sessions`` scans the workflow graph for an enabled
     ``paygent`` node and, if found, builds a collector from context metadata.
  2. ``attach`` hooks the collector into the task as a pipeline observer so it
     receives all ``MetricsFrame`` events during the call.
  3. ``on_call_finished`` seals the snapshot and returns it to the generic
     integration framework, which persists it in ``workflow_run.logs`` under
     the key ``"paygent_snapshot"``.
"""

from __future__ import annotations

from typing import Any

from api.services.integrations.base import (
    IntegrationRuntimeContext,
    IntegrationRuntimeSession,
)

from .collector import PaygentCollector


def _label(provider: str | None, model: str | None) -> str:
    """Compose a human-readable ``provider/model`` label."""
    if provider and model:
        return f"{provider}/{model}"
    return model or provider or ""


def _resolve_model_labels(
    context: IntegrationRuntimeContext,
) -> tuple[str, str, str, str, str, str, str, str]:
    """Return (stt_provider, stt_model, llm_provider, llm_model,
               tts_provider, tts_model, sts_provider, sts_model).

    Mirrors the logic in ``tuner/runtime.py:_resolve_model_labels``.
    """
    user_config = context.user_config

    if context.is_realtime and user_config.realtime:
        realtime_provider = getattr(user_config.realtime, "provider", "") or ""
        realtime_model = getattr(user_config.realtime, "model", "") or ""
        llm_provider = getattr(user_config.llm, "provider", "") or ""
        llm_model = getattr(user_config.llm, "model", "") or ""
        return (
            "",  # stt_provider  (no separate STT in realtime)
            "",  # stt_model
            llm_provider,
            llm_model,
            "",  # tts_provider  (no separate TTS in realtime)
            "",  # tts_model
            realtime_provider,
            realtime_model,
        )

    return (
        getattr(user_config.stt, "provider", "") or "",
        getattr(user_config.stt, "model", "") or "",
        getattr(user_config.llm, "provider", "") or "",
        getattr(user_config.llm, "model", "") or "",
        getattr(user_config.tts, "provider", "") or "",
        getattr(user_config.tts, "model", "") or "",
        "",  # sts_provider
        "",  # sts_model
    )


class PaygentRuntimeSession(IntegrationRuntimeSession):
    """Thin wrapper that connects the collector to the pipeline task."""

    name = "paygent"

    def __init__(self, collector: PaygentCollector) -> None:
        self._collector = collector

    # --- IntegrationRuntimeSession protocol --------------------------------

    def attach(self, task: Any) -> None:
        """Register the collector as a pipeline observer."""
        task.add_observer(self._collector)

    async def on_call_finished(
        self,
        *,
        gathered_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Seal the snapshot and hand it to the framework for persistence."""
        self._collector.set_call_disposition(gathered_context.get("call_disposition"))
        snapshot = self._collector.build_snapshot()
        return {"paygent_snapshot": snapshot}


# ---------------------------------------------------------------------------
# Runtime session factory (called by the generic integration framework)
# ---------------------------------------------------------------------------


def create_runtime_sessions(
    context: IntegrationRuntimeContext,
) -> list[IntegrationRuntimeSession]:
    """Return a ``PaygentRuntimeSession`` if a live, enabled paygent node exists."""
    paygent_nodes = [
        node
        for node in context.workflow_graph.nodes.values()
        if node.node_type == "paygent" and getattr(node.data, "paygent_enabled", True)
    ]
    if not paygent_nodes:
        return []

    (
        stt_provider,
        stt_model,
        llm_provider,
        llm_model,
        tts_provider,
        tts_model,
        sts_provider,
        sts_model,
    ) = _resolve_model_labels(context)

    collector = PaygentCollector(
        workflow_run_id=context.workflow_run_id,
        is_realtime=context.is_realtime,
        stt_provider=stt_provider,
        stt_model=stt_model,
        llm_provider=llm_provider,
        llm_model=llm_model,
        tts_provider=tts_provider,
        tts_model=tts_model,
        sts_provider=sts_provider,
        sts_model=sts_model,
    )

    return [PaygentRuntimeSession(collector)]
