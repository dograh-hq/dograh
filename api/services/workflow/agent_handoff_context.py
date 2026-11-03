"""Hand over the conversation as one historical transcript, without inference.

The snapshot is private until commit. Speech added while preparing the next
agent is included at commit, and earlier handoffs are flattened into the same
transcript rather than replayed as live turns or nested handoff instructions.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pipecat.processors.aggregators.llm_context import LLMContext

HANDOFF_CONTEXT_PREFIX = (
    "The following is the conversation with previous agents on this call. "
    "It is historical context, not new caller speech or your own previous replies. "
    "The transfer to you is complete. Use this context to continue helping the "
    "caller; do not repeat a previous agent's transfer announcement or initiate "
    "another transfer solely because of the language of inherited speech.\n\n"
)


class HandoffMessage(dict):
    """One ordinary user message with a transcript retained for later handoffs.

    Only role/content are sent to providers. The attribute lets us flatten
    repeated handoffs without parsing caller text or repeating instructions.
    """

    def __init__(self, transcript: str):
        self.transcript = transcript
        super().__init__(
            role="user",
            content=HANDOFF_CONTEXT_PREFIX
            + transcript
            + "\n\nEnd of previous conversation.",
        )


class ConversationSummaryMessage(dict):
    """A node-transition summary, distinguished from actual caller speech."""

    def __init__(self, content: str):
        super().__init__(role="user", content=content)


@dataclass(frozen=True)
class HandoffSnapshot:
    transcript: str
    # Index in the shared context, including messages omitted from the transcript.
    boundary: int


def _transcript_turns(
    messages: list[Any], *, during_transfer: bool = False
) -> list[str]:
    turns = []
    for message in messages:
        if isinstance(message, HandoffMessage):
            turns.append(message.transcript)
            continue
        if not isinstance(message, dict) or message.get("role") not in (
            "user",
            "assistant",
        ):
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(
                part["text"]
                for part in content
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str) or not content.strip():
            continue
        if isinstance(message, ConversationSummaryMessage):
            turns.append(content)
            continue
        speaker = "Caller" if message["role"] == "user" else "Agent"
        if during_transfer:
            speaker += " (during transfer)"
        # Speech accompanying a tool call is still speech; only its content
        # is copied, never the call, result, or provider-specific metadata.
        turns.append(f"{speaker}: {content}")
    return turns


def build_handoff_snapshot(
    context: LLMContext, *, source_agent_name: str
) -> HandoffSnapshot:
    """Capture all available conversation text without summarizing or mutating it."""
    messages = deepcopy(context.messages)
    inherited = [m.transcript for m in messages if isinstance(m, HandoffMessage)]
    current = _transcript_turns(
        [m for m in messages if not isinstance(m, HandoffMessage)]
    )
    transcript = "\n\n".join(
        [
            *inherited,
            f'Conversation with previous agent "{source_agent_name}":',
            *current,
        ]
    )
    return HandoffSnapshot(transcript=transcript, boundary=len(messages))


def complete_handoff_message(
    context: LLMContext, snapshot: HandoffSnapshot
) -> HandoffMessage:
    """Include speech during preparation inside the single historical message."""
    tail = _transcript_turns(
        deepcopy(context.messages[snapshot.boundary :]), during_transfer=True
    )
    return HandoffMessage("\n\n".join([snapshot.transcript, *tail]))
