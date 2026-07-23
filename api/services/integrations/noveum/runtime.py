from __future__ import annotations

from typing import Any

from loguru import logger

from api.services.integrations.base import (
    IntegrationRuntimeContext,
    IntegrationRuntimeSession,
)

from .collector import (
    NOVEUM_PAYLOAD_LOG_KEY,
    build_deferred_observer,
    build_payload_envelope,
    make_storage_audio_sink,
)


def _format_model_label(provider: Any, model: Any) -> str:
    # provider/model may be enum members (e.g. ServiceProviders.DOGRAH) —
    # label with the wire value, not the enum repr.
    provider = getattr(provider, "value", provider)
    model = getattr(model, "value", model)
    if provider and model:
        return f"{provider}/{model}"
    if model:
        return model
    return provider or ""


def _resolve_model_labels(context: IntegrationRuntimeContext) -> dict[str, str]:
    user_config = context.user_config

    if context.is_realtime and user_config.realtime:
        llm_model = _format_model_label(
            user_config.realtime.provider, user_config.realtime.model
        )
        return {"stt": "", "llm": llm_model, "tts": ""}

    return {
        "stt": _format_model_label(
            getattr(user_config.stt, "provider", None),
            getattr(user_config.stt, "model", None),
        ),
        "llm": _format_model_label(
            getattr(user_config.llm, "provider", None),
            getattr(user_config.llm, "model", None),
        ),
        "tts": _format_model_label(
            getattr(user_config.tts, "provider", None),
            getattr(user_config.tts, "model", None),
        ),
    }


class NoveumRuntimeSession(IntegrationRuntimeSession):
    name = "noveum"

    def __init__(
        self,
        observer: Any,
        manifest: list[dict[str, Any]],
        call_attributes: dict[str, Any],
    ) -> None:
        self._observer = observer
        self._manifest = manifest
        self._call_attributes = call_attributes

    def attach(self, task: Any) -> None:
        task.add_observer(self._observer)
        # Register turn/latency/audio wiring synchronously (no fire-and-forget
        # task) so nothing races teardown. attach_to_task_sync does NOT start
        # ABP recording (that is async); dograh's on_client_connected already
        # calls audio_buffer.start_recording() before any conversation PCM, so
        # the on_audio_data handler is live in time to capture it.
        self._observer.attach_to_task_sync(task)

    async def on_call_finished(
        self,
        *,
        gathered_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        # This is the SOLE guaranteed finisher: the SDK's on_pipeline_finished
        # safety net is disabled (see collector.build_deferred_observer) so
        # teardown cannot run before dograh's stop_recording() has flushed the
        # conversation audio. on_call_finished is invoked after that flush, and
        # _finish_conversation is idempotent if the EndFrame frame path already
        # finished the trace.
        try:
            await self._observer._finish_conversation()
        except Exception as exc:
            logger.warning(f"Noveum observer finish at call end failed: {exc}")

        attributes = dict(self._call_attributes)
        disposition = gathered_context.get("call_disposition")
        if disposition:
            attributes["dograh.call_disposition"] = disposition

        envelope = build_payload_envelope(self._observer, self._manifest, attributes)
        if envelope is None:
            logger.info("Noveum: no trace captured for this call — nothing to export")
            return None
        trace = envelope.get("trace") or {}
        logger.info(
            f"Noveum: captured trace {trace.get('trace_id')} "
            f"({len(trace.get('spans') or [])} spans, "
            f"{len(self._manifest)} audio segment(s) parked) — "
            "envelope persisted for completion-phase export"
        )
        return {NOVEUM_PAYLOAD_LOG_KEY: envelope}


def create_runtime_sessions(
    context: IntegrationRuntimeContext,
) -> list[IntegrationRuntimeSession]:
    noveum_nodes = [
        node
        for node in context.workflow_graph.nodes.values()
        if node.node_type == "noveum" and getattr(node.data, "noveum_enabled", True)
    ]
    if not noveum_nodes:
        return []

    record_audio = any(
        getattr(node.data, "noveum_record_audio", True) for node in noveum_nodes
    )

    manifest: list[dict[str, Any]] = []
    audio_sink = (
        make_storage_audio_sink(context.workflow_run_id, manifest)
        if record_audio
        else None
    )

    # Observability must degrade to no-observability, never break the call:
    # an SDK import/version failure here would otherwise propagate into
    # run_pipeline and abort call setup.
    try:
        observer = build_deferred_observer(
            record_audio=record_audio,
            audio_sink=audio_sink,
        )
    except Exception as exc:
        logger.error(
            f"Noveum integration disabled for run {context.workflow_run_id}: "
            f"observer construction failed: {exc}"
        )
        return []

    models = _resolve_model_labels(context)
    call_attributes: dict[str, Any] = {
        "dograh.workflow_run_id": context.workflow_run_id,
        "dograh.mode": getattr(context.workflow_run, "mode", None),
        "dograh.agent_version": getattr(
            context.run_definition, "version_number", None
        ),
        "stt.model_label": models["stt"],
        "llm.model_label": models["llm"],
        "tts.model_label": models["tts"],
    }

    logger.info(
        f"Noveum integration active for run {context.workflow_run_id} "
        f"(record_audio={record_audio})"
    )
    return [NoveumRuntimeSession(observer, manifest, call_attributes)]
