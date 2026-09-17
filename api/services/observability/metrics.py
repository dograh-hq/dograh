"""Opt-in, process-local OpenTelemetry metrics exposed for Prometheus scraping.

The SDK aggregates observations in memory; recording never performs network I/O.
Each worker must be scraped directly. This provider is independent of the global
tracer provider used to route conversation traces to Langfuse.
"""

import math
from threading import Lock

from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource

from api.constants import ENABLE_PROMETHEUS_METRICS
from api.services.observability.active_calls import active_call_count

LATENCY_BUCKETS = (
    0.025,
    0.05,
    0.1,
    0.2,
    0.3,
    0.5,
    0.75,
    1,
    1.5,
    2,
    3,
    5,
    10,
    20,
    30,
    60,
)


class RuntimeMetrics:
    def __init__(self):
        # Only load the exporter when the feature is enabled.
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from prometheus_client import CollectorRegistry

        self.registry = CollectorRegistry()
        self._scrape_lock = Lock()
        reader = PrometheusMetricReader(
            registry=self.registry, disable_target_info=True, scope_info_enabled=False
        )
        self.provider = MeterProvider(
            metric_readers=[reader],
            resource=Resource.create({"service.name": "dograh-api"}),
            views=[
                View(
                    instrument_name="dograh_ai_latency",
                    aggregation=ExplicitBucketHistogramAggregation(LATENCY_BUCKETS),
                ),
                View(
                    instrument_name="dograh_response_latency",
                    aggregation=ExplicitBucketHistogramAggregation(LATENCY_BUCKETS),
                ),
            ],
        )
        meter = self.provider.get_meter("dograh.observability")
        meter.create_observable_gauge(
            "dograh_active_calls",
            callbacks=[lambda _options: [Observation(active_call_count())]],
            description=(
                "Voice pipelines running in this worker, including setup and teardown"
            ),
        )
        self.ai_latency = meter.create_histogram(
            "dograh_ai_latency",
            unit="s",
            description="Pipecat service response latency",
        )
        self.response_latency = meter.create_histogram(
            "dograh_response_latency",
            unit="s",
            description="Time from user silence until the bot starts speaking",
        )

    def render(self) -> bytes:
        from prometheus_client import generate_latest

        # Exporter collection and serialization share internal state.
        with self._scrape_lock:
            return generate_latest(self.registry)

    def shutdown(self) -> None:
        self.provider.shutdown()


_runtime: RuntimeMetrics | None = None


def start() -> None:
    """Initialize once per API worker, after fork, from its lifespan."""
    global _runtime
    if _runtime is None and ENABLE_PROMETHEUS_METRICS:
        _runtime = RuntimeMetrics()


def stop() -> None:
    global _runtime
    runtime, _runtime = _runtime, None
    if runtime is not None:
        runtime.shutdown()


def get_runtime() -> RuntimeMetrics | None:
    return _runtime


def valid_duration(seconds: float) -> bool:
    return math.isfinite(seconds) and seconds >= 0
