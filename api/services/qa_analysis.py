"""QA analysis service for post-call quality assessment.

Runs LLM-based analysis on call transcripts, traces under the same
Langfuse trace as the conversation, and returns structured results.
"""

import json
import re
from datetime import datetime
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from api.db import db_client
from api.db.models import WorkflowRunModel
from api.services.gen_ai.json_parser import parse_llm_json
from pipecat.utils.enums import RealtimeFeedbackType


def build_conversation_structure(logs: list[dict]) -> list[dict]:
    """Transform raw call logs into a conversation structure for LLM QA analysis."""
    if not logs:
        return []

    start_time = datetime.fromisoformat(logs[0]["timestamp"])

    conversation = []
    for event in logs:
        if event["type"] == RealtimeFeedbackType.BOT_TEXT.value:
            speaker = "assistant"
            utterance_text = event["payload"]["text"]
            event_time = datetime.fromisoformat(event["payload"]["timestamp"])
        elif event["type"] == RealtimeFeedbackType.USER_TRANSCRIPTION.value and event[
            "payload"
        ].get("final", False):
            speaker = "user"
            utterance_text = event["payload"]["text"]
            event_time = datetime.fromisoformat(event["payload"]["timestamp"])
        else:
            continue

        time_from_start = (event_time - start_time).total_seconds()

        conversation.append(
            {
                "time_from_start_seconds": round(time_from_start, 2),
                "speaker": speaker,
                "text": utterance_text,
                "node_name": event.get("node_name", ""),
                "turn": event.get("turn", 0),
            }
        )

    return conversation


def format_transcript(conversation: list[dict]) -> str:
    """Format conversation structure into a readable transcript string for the LLM."""
    lines = []
    for entry in conversation:
        lines.append(
            f"[{entry['time_from_start_seconds']:.1f}s] "
            f"{entry['speaker']}: {entry['text']}"
        )
    return "\n".join(lines)


def compute_call_metrics(
    logs: list[dict], call_duration_seconds: float | None = None
) -> dict:
    """Pre-compute quantitative metrics from raw call logs."""
    latencies = []
    ttfb_values = []

    for event in logs:
        if event["type"] == RealtimeFeedbackType.LATENCY_MEASURED.value:
            latencies.append(event["payload"]["latency_seconds"])
        elif event["type"] == RealtimeFeedbackType.TTFB_METRIC.value:
            ttfb_values.append(event["payload"]["ttfb_seconds"])

    turns = set()
    for event in logs:
        if event["type"] in (
            RealtimeFeedbackType.USER_TRANSCRIPTION.value,
            RealtimeFeedbackType.BOT_TEXT.value,
        ):
            turns.add(event.get("turn", 0))

    return {
        "call_duration_seconds": call_duration_seconds,
        "num_turns": len(turns),
        "avg_latency_seconds": (
            round(sum(latencies) / len(latencies), 2) if latencies else None
        ),
        "avg_ttfb_seconds": (
            round(sum(ttfb_values) / len(ttfb_values), 2) if ttfb_values else None
        ),
        "max_latency_seconds": round(max(latencies), 2) if latencies else None,
    }


def _extract_trace_id(gathered_context: dict) -> str | None:
    """Extract Langfuse trace_id from gathered_context trace_url.

    URL format: https://langfuse.dograh.com/project/<project_id>/traces/<trace_id>
    """
    trace_url = gathered_context.get("trace_url")
    if not trace_url:
        return None
    try:
        match = re.search(r"/traces/([a-fA-F0-9]+)$", trace_url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def _resolve_llm_config(
    qa_model: str, user_config: dict | None
) -> tuple[str, str, str | None]:
    """Resolve the LLM model, API key, and base URL for QA analysis.

    Returns:
        (model, api_key, base_url) tuple
    """
    llm_config = (user_config or {}).get("llm", {})
    provider = llm_config.get("provider", "openai")
    api_key = llm_config.get("api_key", "")
    base_url = llm_config.get("base_url")

    if qa_model and qa_model != "default":
        model = qa_model
    else:
        model = llm_config.get("model", "gpt-4.1")

    # Set base_url based on provider
    if provider == "openrouter":
        base_url = base_url or "https://openrouter.ai/api/v1"
    elif provider == "groq":
        base_url = "https://api.groq.com/openai/v1"
    elif provider == "google":
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    elif provider == "azure":
        endpoint = llm_config.get("endpoint", "")
        base_url = endpoint if endpoint else None

    return model, api_key, base_url


async def run_qa_analysis(
    qa_node_data: dict[str, Any],
    workflow_run: WorkflowRunModel,
    workflow_run_id: int,
) -> dict[str, Any]:
    """Run QA analysis on a completed workflow run.

    Args:
        qa_node_data: The QA node's data dict from workflow definition
        workflow_run: The workflow run model with logs and context
        workflow_run_id: The workflow run ID

    Returns:
        Dict with tags, summary, score, raw_response
    """
    # Extract transcript from logs
    logs = workflow_run.logs or {}
    rtf_events = logs.get("realtime_feedback_events", [])
    if not rtf_events:
        logger.warning(f"No realtime_feedback_events for run {workflow_run_id}")
        return {"error": "no_transcript", "tags": [], "summary": "", "score": None}

    conversation = build_conversation_structure(rtf_events)
    transcript = format_transcript(conversation)
    if not transcript:
        logger.warning(f"Empty transcript for run {workflow_run_id}")
        return {"error": "empty_transcript", "tags": [], "summary": "", "score": None}

    # Compute call metrics
    usage_info = workflow_run.usage_info or {}
    call_duration = usage_info.get("call_duration_seconds")
    metrics = compute_call_metrics(rtf_events, call_duration)

    # Resolve LLM config
    user_id = None
    if workflow_run.workflow and workflow_run.workflow.user:
        user_id = workflow_run.workflow.user.id

    user_config = None
    if user_id:
        user_configuration = await db_client.get_user_configurations(user_id)
        user_config = user_configuration.model_dump(exclude_none=True)

    qa_model = qa_node_data.get("qa_model", "default")
    system_prompt = qa_node_data.get("qa_system_prompt", "")

    if not system_prompt:
        logger.warning("No system prompt defined for QA Node")
        return {"error": "no_system_prompt", "tags": [], "summary": "", "score": None}

    model, api_key, base_url = _resolve_llm_config(qa_model, user_config)

    if not api_key:
        logger.warning(
            f"No LLM API key configured for QA analysis on run {workflow_run_id}"
        )
        return {"error": "no_api_key", "tags": [], "summary": "", "score": None}

    # Build messages
    system_content = system_prompt.replace("{metrics}", json.dumps(metrics, indent=2))
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"## Transcript\n{transcript}"},
    ]

    # Call LLM
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = AsyncOpenAI(**client_kwargs)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
        )
        raw_response = response.choices[0].message.content
    except Exception as e:
        logger.error(f"QA LLM call failed for run {workflow_run_id}: {e}")
        return {"error": str(e), "tags": [], "summary": "", "score": None}

    # Extract token usage from LLM response
    token_usage = None
    if response.usage:
        token_usage = {
            "prompt_tokens": response.usage.prompt_tokens or 0,
            "completion_tokens": response.usage.completion_tokens or 0,
            "total_tokens": response.usage.total_tokens or 0,
            "cache_read_input_tokens": getattr(
                response.usage, "cache_read_input_tokens", 0
            )
            or 0,
            "cache_creation_input_tokens": getattr(
                response.usage, "cache_creation_input_tokens", None
            ),
        }

    # Parse response
    result: dict[str, Any] = {"raw_response": raw_response, "model": model}
    if token_usage:
        result["token_usage"] = token_usage
    try:
        parsed = parse_llm_json(raw_response)
        result["tags"] = parsed.get("tags", [])
        result["summary"] = parsed.get("summary", "")
        result["score"] = parsed.get("call_quality_score")
        result["overall_sentiment"] = parsed.get("overall_sentiment")
    except (json.JSONDecodeError, ValueError):
        result["tags"] = []
        result["summary"] = ""
        result["score"] = None

    # Langfuse tracing — attach QA generation to the conversation trace
    _add_qa_span_to_conversation_trace(
        workflow_run, model, messages, raw_response, result
    )

    return result


def _add_qa_span_to_conversation_trace(
    workflow_run: WorkflowRunModel,
    model: str,
    messages: list[dict],
    raw_response: str,
    result: dict,
):
    """Attach the QA generation to the existing Langfuse conversation trace.

    Uses OpenTelemetry directly to create a child span under the existing trace,
    matching the same attribute format used by the pipecat pipeline (gen_ai.*).
    """
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            TraceFlags,
            set_span_in_context,
        )

        from api.services.pipecat.tracing_config import (
            is_tracing_enabled,
            setup_tracing_exporter,
        )
        from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

        if not is_tracing_enabled():
            return

        # Ensure the OTEL exporter is initialized (idempotent — no-op if
        # already called in the pipeline process, required in the ARQ worker).
        setup_tracing_exporter()

        gathered_context = workflow_run.gathered_context or {}
        trace_id = _extract_trace_id(gathered_context)
        if not trace_id:
            logger.debug("No trace_id found, skipping Langfuse QA trace")
            return

        tracer = otel_trace.get_tracer("pipecat")

        # Create a remote parent context from the existing trace ID
        parent_span_ctx = SpanContext(
            trace_id=int(trace_id, 16),
            span_id=0x1,  # dummy parent span id
            is_remote=True,
            trace_flags=TraceFlags(0x01),
        )
        parent_ctx = set_span_in_context(NonRecordingSpan(parent_span_ctx))

        # Create a child span under the existing trace
        with tracer.start_as_current_span(
            "qa-analysis",
            context=parent_ctx,
        ) as span:
            add_llm_span_attributes(
                span,
                service_name="OpenAILLMService",
                model=model,
                operation_name="qa-analysis",
                messages=messages,
                output=raw_response,
                stream=False,
                parameters={"temperature": 0},
            )

    except Exception as e:
        logger.warning(f"Failed to trace QA to Langfuse: {e}")
