"""Exercise real OTel aggregation, Prometheus exposition, and Pipecat events."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from pipecat.bus.messages import BusFrameMessage
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    ClientConnectedFrame,
    EndFrame,
    MetricsFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFAMetricsData, TTFATMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import FramePushed
from pipecat.observers.service_metrics_observer import ServiceMetricsObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from prometheus_client.parser import text_string_to_metric_families

from api.routes import main as main_routes
from api.services.observability import active_calls, metrics
from api.services.observability.pipeline_metrics import attach_pipeline_metrics
from api.services.pipecat.agent_bridge import AgentBridgeProcessor
from api.services.pipecat.pipeline_builder import create_pipeline_task
from api.services.pipecat.worker_runner import run_pipeline_worker


@pytest.fixture
def runtime(monkeypatch):
    metrics.stop()
    monkeypatch.setattr(active_calls, "_active_run_ids", set())
    monkeypatch.setattr(metrics, "ENABLE_PROMETHEUS_METRICS", True)
    metrics.start()
    yield metrics.get_runtime()
    metrics.stop()


def samples(runtime, name, **labels):
    return [
        sample
        for family in text_string_to_metric_families(runtime.render().decode())
        for sample in family.samples
        if sample.name == name
        and all(sample.labels.get(key) == value for key, value in labels.items())
    ]


def value(runtime, name, **labels):
    found = samples(runtime, name, **labels)
    assert len(found) == 1, found
    return found[0].value


def pushed(frame):
    return FramePushed(
        source=SimpleNamespace(name="source"),
        destination=SimpleNamespace(name="destination"),
        frame=frame,
        direction=FrameDirection.DOWNSTREAM,
        timestamp=0,
    )


def observed_worker(latency_observer=None, conversation_type="voice"):
    observers = []
    worker = SimpleNamespace(
        add_observer=observers.append,
        user_bot_latency_observer=latency_observer,
    )
    attach_pipeline_metrics(worker, conversation_type=conversation_type)
    return observers


def test_disabled_start_and_repeat_lifecycle_do_not_replace_tracing(monkeypatch):
    metrics.stop()
    tracer = trace.get_tracer_provider()
    monkeypatch.setattr(metrics, "ENABLE_PROMETHEUS_METRICS", False)
    metrics.start()
    assert metrics.get_runtime() is None
    assert observed_worker() == []
    monkeypatch.setattr(metrics, "ENABLE_PROMETHEUS_METRICS", True)
    try:
        metrics.start()
        first = metrics.get_runtime()
        metrics.start()
        assert metrics.get_runtime() is first
        metrics.stop()
        metrics.start()
        assert metrics.get_runtime() is not first
        assert trace.get_tracer_provider() is tracer
    finally:
        metrics.stop()


@pytest.mark.asyncio
async def test_production_worker_wiring_exports_a_metric_through_real_pipeline(runtime):
    class PassThrough(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    worker = create_pipeline_task(
        Pipeline([PassThrough(), PassThrough()]), workflow_run_id=123
    )
    await worker.queue_frames(
        [
            MetricsFrame(
                data=[
                    TTFBMetricsData(
                        processor="OpenAILLMService#999",
                        model="test",
                        value=0.25,
                    )
                ]
            ),
            EndFrame(),
        ]
    )
    await asyncio.wait_for(run_pipeline_worker(worker), timeout=5)
    assert (
        value(runtime, "dograh_ai_latency_seconds_count", service="OpenAILLMService")
        == 1
    )


@pytest.mark.asyncio
async def test_live_call_gauge_survives_duplicate_registration_and_cancellation(
    runtime,
):
    entered = asyncio.Event()

    async def call():
        active_calls.register_active_call(42)
        active_calls.register_active_call(42)
        entered.set()
        try:
            await asyncio.Future()
        finally:
            active_calls.unregister_active_call(42)

    active_calls.register_active_call(43)
    task = asyncio.create_task(call())
    await entered.wait()
    try:
        assert value(runtime, "dograh_active_calls") == 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert value(runtime, "dograh_active_calls") == 1
    active_calls.unregister_active_call(43)
    assert value(runtime, "dograh_active_calls") == 0


@pytest.mark.asyncio
async def test_service_metrics_deduplicate_hops_and_keep_latency_kinds_separate(
    runtime,
):
    observers = observed_worker()
    observer = next(o for o in observers if isinstance(o, ServiceMetricsObserver))
    frame = MetricsFrame(
        data=[
            TTFBMetricsData(
                processor="CartesiaTTSService#12", model="sonic-test", value=0.2
            ),
            TTFAMetricsData(
                processor="CartesiaTTSService#12",
                model="sonic-test",
                ttfa=0.3,
                ttfb=0.2,
                leading_silence=0.1,
            ),
            TTFATMetricsData(
                processor="OpenAILLMService#99",
                model="llm-test",
                ttfat=0.8,
                ttfb=0.3,
                thinking_time=0.5,
            ),
        ]
    )
    await observer.on_push_frame(pushed(frame))
    await observer.on_push_frame(pushed(frame))
    await observer.on_push_frame(
        pushed(
            MetricsFrame(
                data=[
                    TTFBMetricsData(
                        processor="CartesiaTTSService#13", model="sonic-test", value=0.4
                    ),
                ]
            )
        )
    )
    for item in observers:
        await item.cleanup()

    assert value(runtime, "dograh_ai_latency_seconds_count", kind="ttfb") == 2
    assert value(
        runtime, "dograh_ai_latency_seconds_sum", kind="ttfb"
    ) == pytest.approx(0.6)
    assert (
        value(runtime, "dograh_ai_latency_seconds_bucket", kind="ttfb", le="0.3") == 1
    )
    assert value(runtime, "dograh_ai_latency_seconds_count", kind="ttfa") == 1
    assert value(runtime, "dograh_ai_latency_seconds_sum", kind="ttfat") == 0.8
    labels = samples(runtime, "dograh_ai_latency_seconds_count", kind="ttfb")[0].labels
    assert labels["service"] == "CartesiaTTSService"
    assert labels["conversation_type"] == "voice"
    assert not any("#" in v for v in labels.values())
    # Scraping again must not add another observation.
    assert value(runtime, "dograh_ai_latency_seconds_count", kind="ttfb") == 2


@pytest.mark.asyncio
async def test_invalid_durations_are_not_exported_and_text_is_labeled(runtime):
    observers = observed_worker(conversation_type="text")
    assert len(observers) == 1
    observer = observers[0]
    for seconds in (-1, float("nan"), float("inf"), 0.25):
        await observer.on_push_frame(
            pushed(
                MetricsFrame(
                    data=[
                        TTFBMetricsData(processor="call-123::custom", value=seconds),
                    ]
                )
            )
        )
    await observer.cleanup()
    assert (
        value(
            runtime,
            "dograh_ai_latency_seconds_count",
            conversation_type="text",
            service="custom",
            model="unknown",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_metrics_from_retired_agent_reach_observer_once(runtime):
    observer = observed_worker(conversation_type="text")[0]
    bridge = AgentBridgeProcessor(
        bus=SimpleNamespace(),
        worker_name="call",
        selected_visit=lambda: "new-agent",
        allow_inference=lambda: True,
    )

    async def deliver(frame, direction):
        await observer.on_push_frame(pushed(frame))
        await observer.on_push_frame(pushed(frame))

    bridge.push_frame = deliver
    frame = MetricsFrame(
        data=[
            TTFBMetricsData(processor="OpenAILLMService#1", model="test", value=0.5),
        ]
    )
    await bridge.on_bus_message(
        BusFrameMessage(
            source="retired-agent",
            target="call",
            frame=frame,
            direction=FrameDirection.DOWNSTREAM,
        )
    )
    await observer.cleanup()
    assert value(runtime, "dograh_ai_latency_seconds_count") == 1


@pytest.mark.asyncio
async def test_only_user_to_bot_latency_is_exported_from_existing_observer(runtime):
    now = [10.0]
    latency = UserBotLatencyObserver(time_source=lambda: now[0])
    observers = observed_worker(latency_observer=latency)
    assert not any(isinstance(o, UserBotLatencyObserver) for o in observers)
    await latency.on_push_frame(pushed(ClientConnectedFrame()))
    now[0] = 10.5
    await latency.on_push_frame(pushed(BotStartedSpeakingFrame()))
    assert not samples(runtime, "dograh_response_latency_seconds_count")
    now[0] = 11.0
    await latency.on_push_frame(
        pushed(VADUserStoppedSpeakingFrame(timestamp=11, stop_secs=0.2))
    )
    now[0] = 11.8
    await latency.on_push_frame(pushed(BotStartedSpeakingFrame()))
    for observer in [latency, *observers]:
        await observer.cleanup()
    assert not samples(
        runtime, "dograh_response_latency_seconds_count", kind="first_speech"
    )
    assert value(
        runtime, "dograh_response_latency_seconds_sum", kind="user_to_bot"
    ) == pytest.approx(1)


def test_scrape_exports_only_the_three_requested_metric_families(runtime):
    runtime.ai_latency.record(0.2, {"service": "CartesiaTTSService", "kind": "ttfb"})
    runtime.response_latency.record(1.0, {"kind": "user_to_bot"})
    families = text_string_to_metric_families(runtime.render().decode())
    assert {family.name for family in families} == {
        "dograh_active_calls",
        "dograh_ai_latency_seconds",
        "dograh_response_latency_seconds",
    }


@pytest.mark.parametrize(
    "secret,header,status",
    [
        (None, "secret", 503),
        ("secret", None, 403),
        ("secret", "wrong", 403),
        ("secret", "secret", 200),
    ],
)
def test_scrape_requires_ops_secret(runtime, monkeypatch, secret, header, status):
    monkeypatch.setattr("api.constants.DOGRAH_DEVOPS_SECRET", secret)
    app = FastAPI()
    app.add_api_route("/api/v1/metrics", main_routes.prometheus_metrics)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/metrics",
            headers={"X-Dograh-Devops-Secret": header} if header else {},
        )
    assert response.status_code == status
    if status == 200:
        assert response.headers["content-type"].startswith("text/plain")
        assert "dograh_active_calls" in response.text
        assert response.headers["cache-control"] == "no-store"


def test_disabled_scrape_returns_404_and_is_not_in_public_schema(monkeypatch):
    metrics.stop()
    app = FastAPI()
    app.include_router(main_routes.router, prefix="/api/v1")
    with TestClient(app) as client:
        assert client.get("/api/v1/metrics").status_code == 404
    assert "/api/v1/metrics" not in app.openapi()["paths"]
