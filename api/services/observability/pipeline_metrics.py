"""Connect Pipecat's call-scoped observers to the runtime metrics instruments."""

import re

from pipecat.observers.service_metrics_observer import ServiceMetricsObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver

from api.services.observability.metrics import get_runtime, valid_duration


def _service_label(processor: str) -> str:
    # Default Pipecat names are ClassName#123. Never export the instance number
    # or arbitrary application-supplied names (which can contain call/run IDs).
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*Service)(?:#\d+)?", processor)
    return match.group(1) if match else "custom"


def attach_pipeline_metrics(worker, *, conversation_type: str) -> None:
    runtime = get_runtime()
    if runtime is None:
        return

    # One observer on the call worker: AgentBridgeProcessor relays MetricsFrames
    # from all visits here. The upstream observer deduplicates each frame across
    # processor hops. Attaching again to child workers would double-count.
    observer = ServiceMetricsObserver()

    @observer.event_handler("on_service_latency")
    def on_latency(_observer, record):
        if valid_duration(record.seconds):
            runtime.ai_latency.record(
                record.seconds,
                {
                    "service": _service_label(record.processor),
                    "model": record.model or "unknown",
                    "kind": record.kind.value,
                    "conversation_type": conversation_type,
                },
            )

    worker.add_observer(observer)

    if conversation_type != "voice":
        return
    latency_observer = worker.user_bot_latency_observer
    if latency_observer is None:
        # Metrics must also work when trace/turn tracking is disabled.
        latency_observer = UserBotLatencyObserver()
        worker.add_observer(latency_observer)

    @latency_observer.event_handler("on_latency_measured")
    def on_response(_observer, seconds):
        if valid_duration(seconds):
            runtime.response_latency.record(seconds, {"kind": "user_to_bot"})
