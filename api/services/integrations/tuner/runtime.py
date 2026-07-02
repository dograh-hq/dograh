from __future__ import annotations

from typing import Any

from api.services.configuration.registry import ServiceProviders
from api.services.integrations.base import (
    IntegrationRuntimeContext,
    IntegrationRuntimeSession,
)

from .collector import DeferredTunerObserver, mode_to_tuner_call_type


def _format_model_label(provider: str | None, model: str | None) -> str:
    if provider and model:
        return f"{provider}/{model}"
    if model:
        return model
    return provider or ""


def _resolve_model_labels(context: IntegrationRuntimeContext) -> tuple[str, str, str]:
    user_config = context.user_config

    if context.is_realtime and user_config.realtime:
        realtime_provider = user_config.realtime.provider
        realtime_model = user_config.realtime.model
        llm_model = _format_model_label(realtime_provider, realtime_model)
        if realtime_provider in {
            ServiceProviders.GOOGLE_REALTIME.value,
            ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
            ServiceProviders.OPENAI_REALTIME.value,
        }:
            return "", llm_model, ""
        return "", llm_model, ""

    return (
        _format_model_label(
            getattr(user_config.stt, "provider", None),
            getattr(user_config.stt, "model", None),
        ),
        _format_model_label(
            getattr(user_config.llm, "provider", None),
            getattr(user_config.llm, "model", None),
        ),
        _format_model_label(
            getattr(user_config.tts, "provider", None),
            getattr(user_config.tts, "model", None),
        ),
    )


class TunerRuntimeSession(IntegrationRuntimeSession):
    name = "tuner"

    def __init__(self, observer: DeferredTunerObserver) -> None:
        self._observer = observer

    def attach(self, task: Any) -> None:
        self._observer.attach_turn_tracking_observer(task.turn_tracking_observer)
        task.add_observer(self._observer)
        # The SDK Observer wires latency into the accumulator via its own latency
        # observer, which must itself be registered to receive frames.
        task.add_observer(self._observer.latency_observer)

    async def on_call_finished(
        self,
        *,
        gathered_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        self._observer.set_disconnection_reason(
            gathered_context.get("call_disposition")
        )
        payload = self._observer.build_payload_snapshot()
        if payload is None:
            return None
        return {"tuner_payload": payload}


def create_runtime_sessions(
    context: IntegrationRuntimeContext,
) -> list[IntegrationRuntimeSession]:
    tuner_nodes = [
        node
        for node in context.workflow_graph.nodes.values()
        if node.node_type == "tuner" and getattr(node.data, "tuner_enabled", True)
    ]
    if not tuner_nodes:
        return []

    asr_model, llm_model, tts_model = _resolve_model_labels(context)

    observer = DeferredTunerObserver(
        workflow_run_id=context.workflow_run_id,
        call_type=mode_to_tuner_call_type(context.workflow_run.mode),
        asr_model=asr_model,
        llm_model=llm_model,
        tts_model=tts_model,
        agent_version=getattr(context.run_definition, "version_number", None),
    )

    return [TunerRuntimeSession(observer)]
