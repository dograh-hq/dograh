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
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from loguru import logger
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pipecat.frames.frames import LLMContextSummaryRequestFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.context.llm_context_summarization import (
    LLMContextSummarizationUtil,
    LLMContextSummaryConfig,
)
from pipecat.utils.tracing.langfuse_helpers import mark_trace_public
from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

from api.services.pipecat.tracing_config import ensure_tracing

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


@contextmanager
def _compaction_span(llm: Any, request: LLMContextSummaryRequestFrame, parent_context):
    """Trace the out-of-band summary call, or yield ``None`` when tracing is off.

    ``_generate_summary`` reaches the model through ``run_inference``, which
    runs outside the pipeline and so inherits neither the streaming LLM's
    ``@traced_llm`` decorator nor an ambient span. Compaction also runs on its
    own ``TaskGroup`` task, so there is no current span to attach to either --
    hence ``parent_context``, resolved by the caller. Without this, handoff
    compaction is the one summarization in a call that never reaches Langfuse.

    The request attributes are set before the caller awaits, so a timeout or a
    failure still shows what was asked for.
    """
    if not ensure_tracing():
        yield None
        return

    selected = LLMContextSummarizationUtil.get_messages_to_summarize(
        request.context, request.min_messages_to_keep
    )
    transcript = LLMContextSummarizationUtil.format_messages_for_summary(
        selected.messages
    )
    tracer = trace.get_tracer("pipecat")
    with tracer.start_as_current_span(
        "llm-handoff-compaction", context=parent_context
    ) as span:
        # Same per-org policy every other span in the call uses, so compaction
        # is public exactly when the rest of the trace is, and private by
        # default. Matters only when there is no parent to inherit it from.
        mark_trace_public(span)
        # Mirrors what `_generate_summary` sends: the prompt as a system
        # message, the formatted transcript as the user message.
        model = getattr(getattr(llm, "_settings", None), "model", None)
        add_llm_span_attributes(
            span,
            service_name=llm.__class__.__name__,
            model=model if isinstance(model, str) else "unknown",
            operation_name="llm-handoff-compaction",
            messages=[
                {"role": "system", "content": request.summarization_prompt},
                {"role": "user", "content": f"Conversation history:\n{transcript}"},
            ],
            stream=False,
            parameters={"target_context_tokens": request.target_context_tokens},
        )
        yield span


def _record_fallback(span: Any, reason: str) -> None:
    """Mark a compaction that handed over full history instead of a summary."""
    if span is None:
        return
    span.set_attribute("handoff.fallback_reason", reason)
    span.set_status(Status(StatusCode.ERROR, reason))


async def build_handoff_snapshot(
    context: "LLMContext",
    llm: Any,
    *,
    request_id: str,
    retained_messages: int = DEFAULT_RETAINED_MESSAGES,
    timeout: float = DEFAULT_HANDOFF_SUMMARY_TIMEOUT_SECONDS,
    parent_context: Any = None,
) -> HandoffSnapshot:
    """Compact ``context`` for a destination agent without mutating it.

    Args:
        context: The call's shared context. Read only.
        llm: Out-of-band inference client belonging to the *source* agent --
            the summary describes what that agent heard.
        request_id: Identifies this compaction in traces and logs.
        retained_messages: Recent turns kept verbatim behind the summary.
        timeout: Seconds to wait for the summary before falling back.
        parent_context: OTel context the compaction span hangs off. This runs
            on its own task, so there is no ambient span to inherit; without
            it the span becomes a detached root.

    Returns:
        The prepared :class:`HandoffSnapshot`. Summary or tracing failures fall
        back to conversation history. Task cancellation still propagates.
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

    # Span entry formats the transcript and initializes tracing; entry, writes,
    # and teardown must all respect the same fallback as summary generation.
    try:
        with _compaction_span(llm, request, parent_context) as span:
            try:
                summary_text, last_index = await asyncio.wait_for(
                    generate_summary(request), timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Handoff compaction {request_id} timed out after {timeout}s; "
                    "handing over conversation history instead"
                )
                _record_fallback(span, "timeout")
                return fallback
            except asyncio.CancelledError:
                raise
            except Exception:
                _record_fallback(span, "error")
                raise

            if not summary_text or last_index < 0:
                logger.warning(
                    f"Handoff compaction {request_id} produced no summary; handing "
                    "over conversation history instead"
                )
                _record_fallback(span, "empty_summary")
                return fallback

            if span is not None:
                span.set_attribute("output", json.dumps({"content": summary_text}))
    except Exception as e:
        logger.warning(
            f"Handoff compaction {request_id} failed ({e}); handing over "
            "conversation history instead"
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
