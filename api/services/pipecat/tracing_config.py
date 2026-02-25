import base64
import os

from loguru import logger
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from api.constants import (
    ENABLE_TRACING,
    LANGFUSE_HOST,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
)
from pipecat.utils.tracing.setup import setup_tracing

_tracing_initialized = False


def is_tracing_enabled():
    """Check if tracing should be enabled based on ENABLE_TRACING flag."""
    # Tracing is only enabled when ENABLE_TRACING is explicitly set to true
    # This makes the system OSS-friendly by default (no external dependencies required)
    return ENABLE_TRACING


def setup_tracing_exporter():
    """Setup the OTEL tracing exporter for Langfuse if enabled.

    Idempotent — safe to call from both the pipeline process and the ARQ worker.
    """
    global _tracing_initialized
    if _tracing_initialized:
        return

    if is_tracing_enabled():
        if not all([LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY]):
            logger.warning(
                "Warning: ENABLE_TRACING is true but Langfuse credentials are not configured. Tracing disabled."
            )
            return

        langfuse_auth = base64.b64encode(
            f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()
        ).decode()

        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"{LANGFUSE_HOST}/api/public/otel"
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = (
            f"Authorization=Basic {langfuse_auth}"
        )

        otlp_exporter = OTLPSpanExporter()
        setup_tracing(service_name="dograh-pipeline", exporter=otlp_exporter)
        _tracing_initialized = True
