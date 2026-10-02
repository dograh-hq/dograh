"""Integration knowledge added to the conversation before each turn.

An integration that registers context providers is asked for knowledge about the
caller's latest message before the LLM answers it. The answer goes into one
developer message after that user message, so the system prompt and the earlier
history stay unchanged and the provider can reuse its cached prompt prefix. The
next turn replaces the message, and saved or exported conversations leave it out.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from api.services.workflow.agent_handoff_context import (
    ConversationSummaryMessage,
    HandoffMessage,
)
from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

if TYPE_CHECKING:
    from api.services.integrations import ContextProvider
    from api.services.workflow.agent_runtime import AgentRuntime

# A provider that misses this budget is skipped for the turn,
# so a slow integration delays an answer by this much at most.
PROVIDER_TIMEOUT_SECONDS = 0.25


class IntegrationContextMessage(dict):
    """A developer message holding integration knowledge for the current turn only."""


def without_integration_context(messages: list[Any]) -> list[Any]:
    """The conversation without per-turn integration knowledge, for saving or export."""
    return [m for m in messages if not isinstance(m, IntegrationContextMessage)]


def last_user_text(messages: list[Any]) -> str:
    """What the caller said last, or an empty string.

    Handoff transcripts and conversation summaries are user messages that
    the caller did not say, so they are never searched for.
    """
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        if isinstance(message, (HandoffMessage, ConversationSummaryMessage)):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = (
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            return " ".join(parts).strip()
        return ""
    return ""


async def _provide(provider: "ContextProvider", user_text: str) -> str | None:
    try:
        return await asyncio.wait_for(provider(user_text), PROVIDER_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning(f"Integration context skipped for this turn: {exc!r}")
        return None


async def apply_integration_context(agent: "AgentRuntime", context: Any) -> None:
    """Replace the integration knowledge in ``context`` for its latest user message."""
    providers = agent.context_providers
    messages = context.messages
    stale = any(isinstance(m, IntegrationContextMessage) for m in messages)
    if not providers and not stale:
        return
    if stale:
        context.set_messages(without_integration_context(messages))

    user_text = last_user_text(context.messages) if providers else ""
    if not user_text:
        return
    results = await asyncio.gather(
        *(_provide(provider, user_text) for provider in providers)
    )
    blocks = [block for block in results if block]
    if blocks:
        context.add_message(
            IntegrationContextMessage(role="developer", content="\n\n".join(blocks))
        )


class IntegrationContextProcessor(FrameProcessor):
    """Runs integration context providers before the LLM answers a user turn."""

    def __init__(self, agent: "AgentRuntime", **kwargs) -> None:
        super().__init__(**kwargs)
        self._agent = agent

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, LLMContextFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            await apply_integration_context(self._agent, frame.context)
        await self.push_frame(frame, direction)
