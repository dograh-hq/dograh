"""Resolve caller identity and permitted continuity context before Sakinah speaks.

The orchestrator is deliberately the only boundary between stored memories and
the conversational prompt. Callers are not considered verified merely because
their telephone identifier matches a service-user row.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.constants import MEMORY_ENABLED, MEMORY_MAX_RESULTS, MEMORY_MIN_SIMILARITY
from api.db import db_client


def _caller_identifier(context: dict[str, Any]) -> str | None:
    for key in ("caller_identifier", "caller_number", "from_number", "phone_number"):
        value = context.get(key)
        if value:
            return str(value)
    return None


def _verified(context: dict[str, Any]) -> bool:
    return bool(
        context.get("caller_verified")
        or context.get("identity_verified")
        or context.get("service_user_verified")
    )


def _prompt_context(
    caller_status: str,
    preferred_name: str | None,
    memories: list[dict[str, Any]],
) -> str:
    lines = [
        "Caller continuity context is private and must not be read aloud as a list of records.",
        f"Caller state: {caller_status}.",
        "Do not reveal historic details in the opening greeting.",
        "Only refer to a memory when its permissions and the current identity state allow it.",
    ]
    if preferred_name:
        lines.append(f"Preferred name for conversational use: {preferred_name}")
    for memory in memories:
        lines.append(
            "Private memory: "
            f"[{memory['memory_type']}; sensitivity={memory['sensitivity']}; "
            f"verbal_reference_allowed={memory['verbal_reference_allowed']}; "
            f"explicit_detail_allowed={memory['explicit_detail_allowed']}] "
            f"{memory['memory_text']}"
        )
    return "\n".join(lines)


def _permitted_for_prompt(memory: dict[str, Any], *, verified: bool) -> bool:
    if not memory.get("internal_context_allowed", False):
        return False
    if not verified and memory.get("sensitivity") not in {"low", "normal"}:
        return False
    return True


async def prepare_memory_context(
    *,
    organization_id: int | None,
    call_context: dict[str, Any],
) -> dict[str, Any]:
    """Return the bounded caller state that may be passed to Sakinah.

    Storage failures are converted into an UNKNOWN caller result. This keeps
    identity lookup outside the audio/STT/LLM/TTS response path.
    """
    unknown = {
        "caller_status": "UNKNOWN",
        "service_user_id": None,
        "preferred_name": None,
        "memory_available": False,
        "memory_authorisation_level": "none",
        "privacy_safe_previous_summary": None,
        "relevant_memories": [],
        "prompt_context": _prompt_context("UNKNOWN", None, []),
        "greeting_override": (
            "Hello, you're speaking with Sakinah. I'm here to listen and support you.\n"
            "What would you like me to call you, and what would you like to talk about today?"
        ),
    }
    if not MEMORY_ENABLED or not organization_id:
        return unknown

    identifier = _caller_identifier(call_context)
    if not identifier:
        return unknown

    try:
        service_user, created = await db_client.get_or_create_service_user(
            organization_id,
            identifier,
            preferred_name=(
                call_context.get("preferred_name") or call_context.get("caller_name")
            ),
        )
        verified = _verified(call_context)
        caller_status = "VERIFIED" if verified else ("FIRST_TIME" if created else "RECOGNISED")
        memories = [] if created else await db_client.get_permitted_memories(
            service_user.id,
            verified=verified,
            limit=MEMORY_MAX_RESULTS,
            min_similarity=MEMORY_MIN_SIMILARITY,
        )
        memories = [
            memory for memory in memories if _permitted_for_prompt(memory, verified=verified)
        ]
        explanation_needed = created or not service_user.first_use_explanation_shown
        if explanation_needed:
            await db_client.mark_first_use_explanation_shown(service_user.id)

        greeting = (
            f"Hello, {service_user.preferred_name}. Welcome back.\n"
            "Would you like to continue from where we left things, or is there something different you'd like to talk about today?"
            if service_user.preferred_name and caller_status in {"RECOGNISED", "VERIFIED"}
            else unknown["greeting_override"]
        )
        if caller_status == "FIRST_TIME":
            greeting = unknown["greeting_override"]
            if explanation_needed:
                greeting += (
                    "\nI can remember useful information for future conversations, and you can ask me not to remember anything."
                )

        authorization_level = "verified" if verified else "recognised_limited"
        previous_summary = (
            "Returning caller recognised; only low-sensitivity continuity information is available until identity is verified."
            if caller_status == "RECOGNISED"
            else None
        )
        return {
            "caller_status": caller_status,
            "service_user_id": service_user.id,
            "preferred_name": service_user.preferred_name,
            "memory_available": bool(memories),
            "memory_authorisation_level": authorization_level,
            "privacy_safe_previous_summary": previous_summary,
            "relevant_memories": memories,
            "prompt_context": _prompt_context(
                caller_status, service_user.preferred_name, memories
            ),
            "greeting_override": greeting,
        }
    except Exception:
        # Do not include the caller identifier or memory text in logs.
        logger.warning("Memory lookup failed; continuing with an UNKNOWN caller context")
        return unknown
