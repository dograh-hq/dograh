"""Langfuse / OpenTelemetry tracing helpers for QA analysis."""

import json
import re

from loguru import logger

from api.db.models import WorkflowRunModel
from api.services.pipecat.tracing_config import get_trace_url


def extract_trace_id(gathered_context: dict) -> str | None:
    """Extract Langfuse trace_id from gathered_context trace_url.

    Supports both URL formats:
    - New: https://langfuse.dograh.com/trace/<trace_id>
    - Legacy: https://langfuse.dograh.com/project/<project_id>/traces/<trace_id>
    """
    trace_url = gathered_context.get("trace_url")
    if not trace_url:
        return None
    try:
        match = re.search(r"/traces?/([a-fA-F0-9]+)$", trace_url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def setup_langfuse_parent_context(workflow_run: WorkflowRunModel):
    """Set up OTEL parent context from the workflow run's Langfuse trace.

    Returns the parent context object, or None if tracing is unavailable.
    """
    try:
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            TraceFlags,
            set_span_in_context,
        )

        from api.services.pipecat.tracing_config import ensure_tracing

        if not ensure_tracing():
            return None

        gathered_context = workflow_run.gathered_context or {}
        trace_id = extract_trace_id(gathered_context)
        if not trace_id:
            logger.debug("No trace_id found, skipping Langfuse tracing")
            return None

        parent_span_ctx = SpanContext(
            trace_id=int(trace_id, 16),
            span_id=0x1,
            is_remote=True,
            trace_flags=TraceFlags(0x01),
        )
        return set_span_in_context(NonRecordingSpan(parent_span_ctx))

    except Exception as e:
        logger.warning(f"Failed to set up Langfuse parent context: {e}")
        return None


def add_qa_span_to_trace(
    parent_ctx,
    model: str,
    messages: list[dict],
    output: str,
    span_name: str,
    system_prompt: str = "",
) -> None:
    """Create a child span under the conversation trace."""
    if parent_ctx is None:
        return
    try:
        from opentelemetry import trace as otel_trace

        from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

        tracer = otel_trace.get_tracer("pipecat")
        with tracer.start_as_current_span(
            span_name,
            context=parent_ctx,
        ) as span:
            tracing_messages = (
                [
                    {"role": "system", "content": system_prompt},
                    *messages,
                ]
                if system_prompt
                else messages
            )
            add_llm_span_attributes(
                span,
                service_name="OpenAILLMService",
                model=model,
                operation_name=span_name,
                messages=tracing_messages,
                output=json.dumps({"content": output}),
                stream=False,
                parameters={"temperature": 0},
            )
    except Exception as e:
        logger.warning(f"Failed to trace span '{span_name}' to Langfuse: {e}")


def create_node_summary_trace(
    model: str,
    messages: list[dict],
    output: str,
    node_name: str,
    system_prompt: str = "",
) -> str | None:
    """Create a standalone Langfuse trace for a node summary generation.

    Returns the trace URL, or None if tracing is unavailable.
    """
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.context import Context

        from api.services.pipecat.tracing_config import ensure_tracing
        from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

        if not ensure_tracing():
            return None

        tracer = otel_trace.get_tracer("pipecat")

        # Create a root span (new trace) for this node summary generation
        with tracer.start_as_current_span(
            f"node-summary-{node_name}",
            context=Context(),
        ) as span:
            tracing_messages = (
                [
                    {"role": "system", "content": system_prompt},
                    *messages,
                ]
                if system_prompt
                else messages
            )
            add_llm_span_attributes(
                span,
                service_name="OpenAILLMService",
                model=model,
                operation_name=f"node-summary-{node_name}",
                messages=tracing_messages,
                output=json.dumps({"content": output}),
                stream=False,
                parameters={"temperature": 0},
            )
            trace_id = format(span.get_span_context().trace_id, "032x")

        return get_trace_url(trace_id)

    except Exception as e:
        logger.warning(f"Failed to create node summary trace for '{node_name}': {e}")
        return None
