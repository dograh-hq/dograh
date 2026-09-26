"""Asynchronous extraction of bounded long-term memories from completed calls."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from api.constants import MEMORY_EMBEDDING_MODEL, MEMORY_ENABLED
from api.db import db_client
from api.services.configuration.ai_model_configuration import (
    apply_managed_embeddings_base_url,
    get_resolved_ai_model_configuration,
)
from api.services.gen_ai import build_embedding_service
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.pipecat.service_factory import create_llm_service

MEMORY_TYPES = {
    "personal_fact",
    "preference",
    "relationship",
    "ongoing_problem",
    "goal",
    "recent_event",
    "risk_factor",
    "protective_factor",
    "communication_preference",
    "previous_plan",
    "clinical_context",
}
SENSITIVITIES = {"low", "normal", "high", "restricted"}

MEMORY_OPT_OUT_PATTERNS = (
    re.compile(
        r"(?im)^\s*(?:\[[^\]\n]{0,200}\]\s*)?"
        r"(?:user|service user):\s*(?:please\s+)?"
        r"(?:do not|don't|never)\s+(?:remember|save|store|retain|keep)\b"
    ),
    re.compile(
        r"(?i)\bi\s+(?:do not|don't)\s+want\s+"
        r"(?:you|sakinah|this service)\s+to\s+"
        r"(?:remember|save|store|retain|keep)\b"
    ),
    re.compile(
        r"(?i)\bi\s+(?:do not|don't)\s+want\s+"
        r"(?:this|that|anything|what i (?:said|say))\s+"
        r"(?:remembered|saved|stored|retained|kept)\b"
    ),
    re.compile(
        r"(?im)^\s*(?:\[[^\]\n]{0,200}\]\s*)?"
        r"(?:user|service user):\s*(?:please\s+)?forget\s+"
        r"(?:this|that|everything|what i (?:said|say))\b"
    ),
)


def _transcript_for_prompt(utterances: list[Any], transcript: str | None) -> str:
    if transcript:
        return transcript[-30_000:]
    return "\n".join(
        f"{item.speaker.upper()}: {item.transcript}" for item in utterances
    )[-30_000:]


def _parse_proposals(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = parse_llm_json(raw)
    except Exception:  # noqa: BLE001 - tolerate provider-specific JSON wrappers
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if isinstance(parsed, dict):
        parsed = parsed.get("memories", [])
    return parsed if isinstance(parsed, list) else []


def _memory_storage_allowed(raw: str | None) -> bool | None:
    """Return only an explicit model-classified opt-out/permission value."""
    if not raw:
        return None
    try:
        parsed = parse_llm_json(raw)
    except Exception:  # noqa: BLE001 - tolerate provider-specific JSON wrappers
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("memory_storage_allowed")
    return value if isinstance(value, bool) else None


def memory_opt_out_requested(transcript: str) -> bool:
    """Recognise explicit caller requests without mistaking memory-loss speech."""
    normalized = transcript.replace("’", "'")
    return any(pattern.search(normalized) for pattern in MEMORY_OPT_OUT_PATTERNS)


async def _record_memory_opt_out(run: Any) -> None:
    await db_client.record_memory_opt_out(
        organization_id=run.workflow.organization_id,
        service_user_id=run.service_user_id,
        source_workflow_run_id=run.id,
        verification_level=("verified" if run.caller_state == "VERIFIED" else "none"),
    )


def _safe_proposal(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    text = str(value.get("memory_text") or value.get("text") or "").strip()
    memory_type = str(value.get("memory_type") or value.get("type") or "").strip()
    if not text or len(text) > 2_000 or memory_type not in MEMORY_TYPES:
        return None
    sensitivity = str(value.get("sensitivity") or "normal").lower()
    if sensitivity not in SENSITIVITIES:
        sensitivity = "normal"
    try:
        importance = min(1.0, max(0.0, float(value.get("importance", 0.5))))
        confidence = min(1.0, max(0.0, float(value.get("confidence", 0.5))))
    except (TypeError, ValueError):
        importance = confidence = 0.5
    try:
        expires_days = (
            int(value["expires_days"])
            if value.get("expires_days") is not None
            else None
        )
    except (TypeError, ValueError):
        expires_days = None
    if expires_days is not None:
        expires_days = min(3650, max(1, expires_days))
    # High/restricted facts are useful as private context only until a verified
    # identity and an explicit permission flow says otherwise.
    if sensitivity in {"high", "restricted"}:
        verbal_reference_allowed = False
        explicit_detail_allowed = False
    else:
        verbal_reference_allowed = bool(value.get("verbal_reference_allowed", False))
        explicit_detail_allowed = bool(value.get("explicit_detail_allowed", False))
    return {
        "memory_text": text,
        "memory_type": memory_type,
        "importance": importance,
        "confidence": confidence,
        "sensitivity": sensitivity,
        "expires_at": (
            datetime.now(UTC) + timedelta(days=expires_days)
            if expires_days is not None
            else None
        ),
        "verbal_reference_allowed": verbal_reference_allowed,
        "explicit_detail_allowed": explicit_detail_allowed,
        "source_utterance_sequence": value.get("source_utterance_sequence"),
    }


async def _embed_memories(
    organization_id: int,
    texts: list[str],
) -> list[list[float] | None]:
    try:
        resolved = await get_resolved_ai_model_configuration(
            organization_id=organization_id
        )
        embeddings = resolved.effective.embeddings
        if embeddings is None:
            return [None for _ in texts]
        service = await build_embedding_service(
            db_client=db_client,
            provider=getattr(embeddings, "provider", None),
            api_key=getattr(embeddings, "api_key", None),
            model=getattr(embeddings, "model", None) or MEMORY_EMBEDDING_MODEL,
            base_url=apply_managed_embeddings_base_url(
                provider=getattr(embeddings, "provider", None),
                base_url=getattr(embeddings, "base_url", None),
            ),
            endpoint=getattr(embeddings, "endpoint", None),
            api_version=getattr(embeddings, "api_version", None),
            resolve_correlation=True,
        )
        vectors = await service.embed_texts(texts)
        return [vector if len(vector) == 1536 else None for vector in vectors]
    except Exception:  # noqa: BLE001 - embeddings are optional persistence enrichment
        logger.warning(
            "Memory embedding failed; retaining text memory without a vector"
        )
        return [None for _ in texts]


async def extract_and_store_memories(workflow_run_id: int) -> int:
    """Extract and persist useful memories without exposing the whole history."""
    run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if (
        not MEMORY_ENABLED
        or run is None
        or not run.service_user_id
        or not run.workflow
        or run.workflow.name != "Sakinah Scenario Console"
    ):
        return 0
    if not await db_client.is_memory_permitted(
        run.service_user_id, permission_type="memory_storage"
    ):
        return 0
    utterances = await db_client.get_utterances_for_run(workflow_run_id)
    transcript = _transcript_for_prompt(utterances, run.full_transcript)
    if not transcript:
        return 0

    if memory_opt_out_requested(transcript):
        try:
            await _record_memory_opt_out(run)
        except Exception:  # noqa: BLE001 - post-call failure must not affect the call
            logger.warning("Memory opt-out could not be persisted for completed call")
        return 0

    resolved = await get_resolved_ai_model_configuration(
        organization_id=run.workflow.organization_id
    )
    if resolved.effective.llm is None:
        return 0
    system_prompt = (
        "Extract only useful, likely-long-term continuity facts from the transcript. "
        "Do not extract every utterance, greetings, transient small talk, or "
        "unsupported inference. "
        "If the caller explicitly asks not to be remembered, set "
        "memory_storage_allowed to false. "
        'Return JSON only: {"memory_storage_allowed":boolean,"memories":['
        '{"memory_text":string,"memory_type":string,'
        '"importance":number,"confidence":number,"sensitivity":"low"|"normal"|"high"|"restricted",'
        '"source_utterance_sequence":number|null,"expires_days":number|null,'
        '"verbal_reference_allowed":boolean,"explicit_detail_allowed":boolean}]} .'
    )
    try:
        llm = create_llm_service(resolved.effective, usage_context="memory_extraction")
        context = LLMContext()
        context.set_messages([{"role": "user", "content": transcript}])
        raw = await llm.run_inference(
            context, max_tokens=2500, system_instruction=system_prompt
        )
    except Exception:  # noqa: BLE001 - model extraction is best-effort post-call work
        logger.warning("Memory extraction failed for completed call")
        return 0

    if _memory_storage_allowed(raw) is False:
        try:
            await _record_memory_opt_out(run)
        except Exception:  # noqa: BLE001 - post-call failure must not affect the call
            logger.warning("Memory opt-out could not be persisted for completed call")
        return 0

    proposals = [
        item
        for item in (_safe_proposal(value) for value in _parse_proposals(raw))
        if item
    ]
    proposals = proposals[:20]
    vectors = await _embed_memories(
        run.workflow.organization_id,
        [item["memory_text"] for item in proposals],
    )
    sequence_to_id = {item.sequence_number: item.id for item in utterances}
    stored = 0
    for proposal, vector in zip(proposals, vectors):
        source_sequence = proposal.pop("source_utterance_sequence", None)
        try:
            source_sequence = (
                int(source_sequence) if source_sequence is not None else None
            )
        except (TypeError, ValueError):
            source_sequence = None
        proposal.update(
            {
                "service_user_id": run.service_user_id,
                "source_agent_run_id": workflow_run_id,
                "source_utterance_id": sequence_to_id.get(source_sequence),
                "embedding": vector,
            }
        )
        try:
            await db_client.create_or_confirm_memory(proposal)
            stored += 1
        except Exception:  # noqa: BLE001 - one bad proposal must not discard others
            logger.warning("A proposed memory could not be persisted")
    return stored
