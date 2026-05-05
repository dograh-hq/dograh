"""Main QA analysis orchestrator — per-node and whole-call fallback."""

import json
from typing import Any

from loguru import logger

from api.db.models import WorkflowRunModel
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.workflow.dto import QANodeData
from api.services.workflow.qa.conversation import (
    build_conversation_structure,
    format_transcript,
    split_events_by_node,
)
from api.services.workflow.qa.llm_config import resolve_llm_config
from api.services.workflow.qa.metrics import compute_call_metrics
from api.services.workflow.qa.node_summary import (
    CONVERSATION_SUMMARY_SYSTEM_PROMPT,
    ensure_node_summaries,
    get_node_summary_text,
)
from api.services.workflow.qa.tracing import (
    add_qa_span_to_trace,
    setup_langfuse_parent_context,
)
from api.utils.template_renderer import render_template
from pipecat.processors.aggregators.llm_context import LLMContext


async def _run_llm_inference(
    llm, messages: list[dict], system_prompt: str
) -> str | None:
    """Run a one-shot LLM inference using the pipecat service."""
    context = LLMContext()
    context.set_messages(messages)
    return await llm.run_inference(context, system_instruction=system_prompt)


async def _generate_conversation_summary(
    llm,
    model: str,
    transcript: str,
    parent_ctx,
    node_name: str,
) -> str:
    """Generate a summary of the conversation so far (before the current node).

    Traced to Langfuse as conversation-summary-before-{node_name}.
    """
    messages = [
        {"role": "user", "content": f"## Conversation\n{transcript}"},
    ]

    try:
        summary = (
            await _run_llm_inference(llm, messages, CONVERSATION_SUMMARY_SYSTEM_PROMPT)
            or ""
        )

        span_name = f"conversation-summary-before-{node_name}"
        add_qa_span_to_trace(
            parent_ctx,
            model,
            messages,
            summary,
            span_name,
            CONVERSATION_SUMMARY_SYSTEM_PROMPT,
        )

        return summary
    except Exception as e:
        logger.warning(
            f"Failed to generate conversation summary before {node_name}: {e}"
        )
        return ""


async def run_per_node_qa_analysis(
    qa_data: QANodeData,
    workflow_run: WorkflowRunModel,
    workflow_run_id: int,
    workflow_definition: dict,
    definition_id: int | None,
) -> dict[str, Any]:
    """Run per-node QA analysis on a completed workflow run.

    Splits the call by node, generates per-node summaries and conversation
    context, then evaluates each node segment individually.

    Falls back to whole-call QA if events lack node_id.

    Returns:
        Dict with node_results, model
    """
    logs = workflow_run.logs or {}
    rtf_events = logs.get("realtime_feedback_events", [])
    if not rtf_events:
        logger.warning(f"No realtime_feedback_events for run {workflow_run_id}")
        return {"error": "no_transcript", "node_results": {}}

    # Try to split by node
    node_splits = split_events_by_node(rtf_events)
    if not node_splits:
        # Fall back to whole-call QA
        logger.info(
            f"Events lack node_id for run {workflow_run_id}, falling back to whole-call QA"
        )
        return await _run_whole_call_qa_analysis(qa_data, workflow_run, workflow_run_id)

    system_prompt = qa_data.qa_system_prompt or ""
    if not system_prompt:
        logger.warning("No system prompt defined for QA Node")
        return {"error": "no_system_prompt", "node_results": {}}

    # Resolve LLM config
    provider, model, api_key, service_kwargs = await resolve_llm_config(
        qa_data, workflow_run
    )
    if not api_key:
        logger.warning(
            f"No LLM API key configured for QA analysis on run {workflow_run_id}"
        )
        return {"error": "no_api_key", "node_results": {}}

    # Ensure node summaries
    node_summaries = await ensure_node_summaries(
        workflow_definition, definition_id, workflow_run, qa_data
    )

    # Set up Langfuse tracing
    parent_ctx = setup_langfuse_parent_context(workflow_run)

    # Build LLM service
    llm = create_llm_service_from_provider(provider, model, api_key, **service_kwargs)

    node_results: dict[str, Any] = {}
    prior_conversation: list[dict] = []  # Running accumulation of all prior nodes

    for idx, (node_id, node_name, node_events) in enumerate(node_splits):
        # Build this node's conversation and transcript
        node_conversation = build_conversation_structure(node_events)
        node_transcript = format_transcript(node_conversation)
        if not node_transcript:
            continue

        # Compute per-node metrics
        node_metrics = compute_call_metrics(node_events)

        # Get node summary
        node_summary = get_node_summary_text(node_summaries, node_id)

        # Generate conversation summary from prior nodes (if not first)
        previous_conversation_summary = ""
        if idx > 0 and prior_conversation:
            prior_transcript = format_transcript(prior_conversation)
            previous_conversation_summary = await _generate_conversation_summary(
                llm,
                model,
                prior_transcript,
                parent_ctx,
                node_name,
            )

        # Substitute placeholders in the user's system prompt
        template_context = {
            "node_summary": node_summary,
            "previous_conversation_summary": previous_conversation_summary,
            "transcript": node_transcript,
            "metrics": json.dumps(node_metrics, indent=2),
        }
        system_content = render_template(system_prompt, template_context)

        messages = [
            {"role": "user", "content": f"## Transcript\n{node_transcript}"},
        ]

        # Call QA LLM
        try:
            raw_response = await _run_llm_inference(llm, messages, system_content)
        except Exception as e:
            logger.error(
                f"QA LLM call failed for node '{node_name}' on run {workflow_run_id}: {e}"
            )
            node_results[node_id] = {
                "node_name": node_name,
                "error": str(e),
                "tags": [],
                "summary": "",
                "score": None,
            }
            prior_conversation.extend(node_conversation)
            continue

        # Trace
        span_name = f"qa-node-{node_name}"
        add_qa_span_to_trace(
            parent_ctx, model, messages, raw_response, span_name, system_content
        )

        # Parse response
        node_result: dict[str, Any] = {
            "node_name": node_name,
            "raw_response": raw_response,
        }
        try:
            parsed = parse_llm_json(raw_response)
            node_result["tags"] = parsed.get("tags", [])
            node_result["summary"] = parsed.get("summary", "")
            node_result["score"] = parsed.get("call_quality_score")
            node_result["overall_sentiment"] = parsed.get("overall_sentiment")
        except (json.JSONDecodeError, ValueError):
            node_result["tags"] = []
            node_result["summary"] = ""
            node_result["score"] = None

        node_results[node_id] = node_result

        # Append this node's conversation to running total
        prior_conversation.extend(node_conversation)

    return {
        "node_results": node_results,
        "model": model,
    }


async def _run_whole_call_qa_analysis(
    qa_data: QANodeData,
    workflow_run: WorkflowRunModel,
    workflow_run_id: int,
) -> dict[str, Any]:
    """Run whole-call QA analysis (fallback when events lack node_id).

    Returns results wrapped in the per-node format for consistency.
    """
    logs = workflow_run.logs or {}
    rtf_events = logs.get("realtime_feedback_events", [])
    if not rtf_events:
        logger.warning(f"No realtime_feedback_events for run {workflow_run_id}")
        return {"error": "no_transcript", "node_results": {}}

    conversation = build_conversation_structure(rtf_events)
    transcript = format_transcript(conversation)
    if not transcript:
        logger.warning(f"Empty transcript for run {workflow_run_id}")
        return {"error": "empty_transcript", "node_results": {}}

    # Compute call metrics
    usage_info = workflow_run.usage_info or {}
    call_duration = usage_info.get("call_duration_seconds")
    metrics = compute_call_metrics(rtf_events, call_duration)

    # Resolve LLM config
    system_prompt = qa_data.qa_system_prompt or ""
    if not system_prompt:
        logger.warning("No system prompt defined for QA Node")
        return {"error": "no_system_prompt", "node_results": {}}

    provider, model, api_key, service_kwargs = await resolve_llm_config(
        qa_data, workflow_run
    )

    if not api_key:
        logger.warning(
            f"No LLM API key configured for QA analysis on run {workflow_run_id}"
        )
        return {"error": "no_api_key", "node_results": {}}

    # Build messages — substitute all placeholders with sensible defaults
    template_context = {
        "node_summary": "",
        "previous_conversation_summary": "",
        "transcript": transcript,
        "metrics": json.dumps(metrics, indent=2),
    }
    system_content = render_template(system_prompt, template_context)
    messages = [
        {"role": "user", "content": f"## Transcript\n{transcript}"},
    ]

    # Call LLM
    llm = create_llm_service_from_provider(provider, model, api_key, **service_kwargs)

    try:
        raw_response = await _run_llm_inference(llm, messages, system_content)
    except Exception as e:
        logger.error(f"QA LLM call failed for run {workflow_run_id}: {e}")
        return {"error": str(e), "node_results": {}}

    # Parse response
    node_result: dict[str, Any] = {
        "node_name": "whole_call",
        "raw_response": raw_response,
    }
    try:
        parsed = parse_llm_json(raw_response)
        node_result["tags"] = parsed.get("tags", [])
        node_result["summary"] = parsed.get("summary", "")
        node_result["score"] = parsed.get("call_quality_score")
        node_result["overall_sentiment"] = parsed.get("overall_sentiment")
    except (json.JSONDecodeError, ValueError):
        node_result["tags"] = []
        node_result["summary"] = ""
        node_result["score"] = None

    # Langfuse tracing
    parent_ctx = setup_langfuse_parent_context(workflow_run)
    add_qa_span_to_trace(
        parent_ctx, model, messages, raw_response, "qa-analysis", system_content
    )

    return {
        "node_results": {"whole_call": node_result},
        "model": model,
    }
