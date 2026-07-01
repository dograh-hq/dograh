"""Post-call interest classification: did the callee show interest (yes/no)?

A single cheap LLM pass over the call transcript, independent of QA nodes, so
every campaign call gets a lead-qualification signal. The result lands in
``workflow_run.annotations["interested"]`` so campaign runs can surface + filter
it. Best-effort — never raises into post-call processing.
"""

from typing import Any, Optional

from loguru import logger

from api.db.models import WorkflowRunModel
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.managed_model_services import get_mps_correlation_id
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.workflow.qa.analysis import _run_llm_inference
from api.services.workflow.qa.conversation import (
    build_conversation_structure,
    format_transcript,
)
from api.services.workflow.qa.llm_config import resolve_user_llm_config

INTEREST_SYSTEM_PROMPT = """You classify whether the person the AI agent CALLED (the customer, not the agent) showed interest in the offer/product/service discussed on the call.

Read the transcript and decide from the CUSTOMER's side:
- "yes" — clear positive interest: agreed, asked to proceed, requested a callback/demo/details/quote, gave buying signals, booked, or said they want it.
- "no" — clearly declined, said not interested, hung up early, was hostile, or it was a wrong number / voicemail / no real conversation.
- "unclear" — genuinely ambiguous: only pleasantries, undecided, "maybe later", or cut off before any signal.

When unsure between yes and unclear, prefer "unclear". Respond with ONLY a JSON object, no prose:
{"interested": "yes" | "no" | "unclear", "reason": "<one short sentence>"}"""


async def classify_call_interest(
    workflow_run: WorkflowRunModel,
) -> Optional[dict[str, Any]]:
    """Classify the callee's interest from the transcript.

    Returns ``{"value": "yes"|"no"|"unclear", "reason": str}`` or ``None`` when
    it can't be determined (no transcript, no LLM key, bad response). Never raises.
    """
    try:
        logs = getattr(workflow_run, "logs", None) or {}
        rtf_events = logs.get("realtime_feedback_events", [])
        if not rtf_events:
            return None
        transcript = format_transcript(build_conversation_structure(rtf_events))
        if not transcript or not transcript.strip():
            return None

        provider, model, api_key, kwargs = await resolve_user_llm_config(workflow_run)
        if not api_key:
            logger.info("Interest classification skipped — no LLM API key")
            return None

        correlation_id = get_mps_correlation_id(
            getattr(workflow_run, "initial_context", None)
        )
        llm = create_llm_service_from_provider(
            provider, model, api_key, correlation_id=correlation_id, **kwargs
        )
        messages = [{"role": "user", "content": f"## Transcript\n{transcript}"}]
        raw = await _run_llm_inference(llm, messages, INTEREST_SYSTEM_PROMPT)
        parsed = parse_llm_json(raw) if raw else None
        if not isinstance(parsed, dict):
            return None
        value = str(parsed.get("interested", "")).strip().lower()
        if value not in ("yes", "no", "unclear"):
            return None
        return {"value": value, "reason": str(parsed.get("reason", "")).strip()[:280]}
    except Exception as e:  # noqa: BLE001 — best-effort, never break post-call
        logger.warning(f"Interest classification failed: {e}")
        return None
