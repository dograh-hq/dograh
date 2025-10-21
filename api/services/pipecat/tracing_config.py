import base64
import os

from loguru import logger
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from api.constants import ENABLE_TRACING
from pipecat.utils.tracing.setup import setup_tracing


def is_tracing_enabled():
    """Check if tracing should be enabled based on ENABLE_TRACING flag."""
    # Tracing is only enabled when ENABLE_TRACING is explicitly set to true
    # This makes the system OSS-friendly by default (no external dependencies required)
    return ENABLE_TRACING


def setup_pipeline_tracing():
    """Setup tracing for the pipeline if enabled"""
    if is_tracing_enabled():
        # Only set up Langfuse if credentials are provided
        langfuse_host = os.environ.get("LANGFUSE_HOST")
        langfuse_public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        langfuse_secret_key = os.environ.get("LANGFUSE_SECRET_KEY")

        if not all([langfuse_host, langfuse_public_key, langfuse_secret_key]):
            logger.warning(
                "Warning: ENABLE_TRACING is true but Langfuse credentials are not configured. Tracing disabled."
            )
            return

        LANGFUSE_AUTH = base64.b64encode(
            f"{langfuse_public_key}:{langfuse_secret_key}".encode()
        ).decode()

        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"{langfuse_host}/api/public/otel"
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = (
            f"Authorization=Basic {LANGFUSE_AUTH}"
        )

        otlp_exporter = OTLPSpanExporter()
        setup_tracing(service_name="dograh-pipeline", exporter=otlp_exporter)
