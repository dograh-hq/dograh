"""Compacting the conversation for handover to another agent.

The call keeps one ``LLMContext`` object for its whole life; a handoff replaces
its *contents* at a controlled moment. What the destination agent receives is:

    summary of everything through the snapshot boundary
  + the most recent turns, kept verbatim
  + whatever the caller said after the boundary, added at commit time

``ContextSummarizationManager`` cannot serve here. Its ``start()`` schedules a
fire-and-forget mutation of the live context *and* cancels any summarization
already in flight, so the destination's first ``set_node`` would kill a handoff
compaction still running. This is a plain awaitable that returns messages and
never touches the context, and it runs on its own task so the two cannot
cancel each other.

The destination receives conversation text. Source tool calls and results are
filtered out because they belong to the previous agent's execution context.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from loguru import logger
from pipecat.frames.frames import LLMContextSummaryRequestFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.context.llm_context_summarization import LLMContextSummaryConfig

# Messages kept verbatim behind the summary. Enough for the destination to
# answer "as I was saying" without re-reading the whole call.
DEFAULT_RETAINED_MESSAGES = 6

DEFAULT_HANDOFF_SUMMARY_TIMEOUT_SECONDS = 8.0

HANDOFF_SUMMARY_PROMPT = (
    "You are preparing a handover note for a colleague who is about to take "
    "over this live phone call. Summarize what the caller wants, every fact "
    "they gave (names, numbers, dates, addresses, account or reference "
    "identifiers -- reproduce these exactly), what has been promised or "
    "agreed, and what is still outstanding. Write it as a briefing for the "
    "colleague, not as a transcript. Be concise and do not invent anything."
)

HANDOFF_SUMMARY_TEMPLATE = (
    "Handover note from the previous agent on this same call: {summary}"
)


@dataclass(frozen=True)
class HandoffSnapshot:
    """Conversation prepared for a destination agent.

    Attributes:
        messages: What the destination starts from, oldest first.
        boundary: Index into the source context's message list at the moment
            the snapshot was taken. Anything the caller said after this is
            appended at commit, so speech during the hold is not lost.
        summarized: Whether a summary was produced, as opposed to the full-history fallback.
    """

    messages: list[Any]
    boundary: int
    summarized: bool


def _is_standard_message(message: Any) -> bool:
    return isinstance(message, dict)


def _carries_tool_traffic(message: Any) -> bool:
    """Whether a message describes tool work rather than the conversation."""
    if not _is_standard_message(message):
        # LLM-specific messages are opaque and provider-shaped; they belong to
        # the agent that produced them.
        return True
    if message.get("role") in ("tool", "function", "system"):
        return True
    return bool(message.get("tool_calls") or message.get("function_call"))


def conversation_messages(messages: list[Any]) -> list[Any]:
    """Keep only caller/agent turns that a different agent can safely read."""
    kept: list[Any] = []
    for message in messages:
        if _carries_tool_traffic(message):
            continue
        if not message.get("content"):
            continue
        kept.append(message)
    return kept


async def build_handoff_snapshot(
    context: "LLMContext",
    llm: Any,
    *,
    request_id: str,
    retained_messages: int = DEFAULT_RETAINED_MESSAGES,
    timeout: float = DEFAULT_HANDOFF_SUMMARY_TIMEOUT_SECONDS,
) -> HandoffSnapshot:
    """Compact ``context`` for a destination agent without mutating it.

    Args:
        context: The call's shared context. Read only.
        llm: Out-of-band inference client belonging to the *source* agent --
            the summary describes what that agent heard.
        request_id: Identifies this compaction in traces and logs.
        retained_messages: Recent turns kept verbatim behind the summary.
        timeout: Seconds to wait for the summary before falling back.

    Returns:
        The prepared :class:`HandoffSnapshot`. Never raises: a handoff that
        cannot summarize proceeds with conversation history rather than failing.
    """
    source_messages = deepcopy(context.messages)
    boundary = len(source_messages)
    conversation = conversation_messages(source_messages)

    if not conversation:
        return HandoffSnapshot(messages=[], boundary=boundary, summarized=False)

    fallback = HandoffSnapshot(
        messages=conversation,
        boundary=boundary,
        summarized=False,
    )

    generate_summary = getattr(llm, "_generate_summary", None)
    if generate_summary is None or len(conversation) <= retained_messages:
        return fallback

    config = LLMContextSummaryConfig(
        target_context_tokens=2000,
        min_messages_after_summary=retained_messages,
        summarization_prompt=HANDOFF_SUMMARY_PROMPT,
        summary_message_template=HANDOFF_SUMMARY_TEMPLATE,
        summarization_timeout=timeout,
    )
    request = LLMContextSummaryRequestFrame(
        request_id=request_id,
        context=LLMContext(messages=source_messages),
        min_messages_to_keep=retained_messages,
        target_context_tokens=config.target_context_tokens,
        summarization_prompt=config.summary_prompt,
        summarization_timeout=timeout,
    )

    try:
        summary_text, last_index = await asyncio.wait_for(
            generate_summary(request), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"Handoff compaction {request_id} timed out after {timeout}s; "
            "handing over conversation history instead"
        )
        return fallback
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            f"Handoff compaction {request_id} failed ({e}); handing over "
            "conversation history instead"
        )
        return fallback

    if not summary_text or last_index < 0:
        logger.warning(
            f"Handoff compaction {request_id} produced no summary; handing "
            "over conversation history instead"
        )
        return fallback

    last_index = min(last_index, boundary - 1)
    retained = conversation_messages(source_messages[last_index + 1 :])
    messages = [
        {
            "role": "user",
            "content": config.summary_message_template.format(summary=summary_text),
        },
        *retained,
    ]
    logger.info(
        f"Handoff compaction {request_id}: {len(source_messages)} messages -> "
        f"summary + {len(retained)} retained"
    )
    return HandoffSnapshot(messages=messages, boundary=boundary, summarized=True)


def messages_after_boundary(context: "LLMContext", boundary: int) -> list[Any]:
    """Caller turns committed after a snapshot was taken.

    The caller keeps talking while the destination is being prepared. Those
    turns are outside the summary, so they are appended at commit -- once,
    filtered the same way, so the destination never sees the previous agent's
    tool traffic.
    """
    return conversation_messages(deepcopy(context.messages)[boundary:])
