"""Integration knowledge composed into the system instruction before each turn.

An integration that registers context providers is asked for knowledge about the
caller's latest message before the LLM answers it. The result is appended to the
node prompt until the next user turn, so the answer needs no tool call round trip.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import LLMSettings

if TYPE_CHECKING:
    from api.services.integrations import ContextProvider
    from api.services.workflow.agent_runtime import AgentRuntime

# A provider that misses this budget is skipped for the turn,
# so a slow integration delays an answer by this much at most.
PROVIDER_TIMEOUT_SECONDS = 0.25


def last_user_text(messages: list[Any]) -> str:
    """The text of the newest user message in an LLM context, or an empty string."""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
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


def compose_system_instruction(prompt: str, blocks: list[str]) -> str:
    """The node prompt followed by the integration knowledge for the current turn."""
    return "\n\n".join([prompt, *blocks])


async def apply_integration_context(agent: "AgentRuntime", context: Any) -> None:
    """Refresh the integration knowledge in the agent's system instruction for this turn.

    The knowledge stays with the agent until the next user turn, so a node
    transition inside the turn keeps it in the new node's instruction.
    """
    providers = agent.context_providers
    if not providers and not agent.context_blocks:
        return

    blocks: list[str] = []
    user_text = last_user_text(context.messages) if providers else ""
    if user_text:
        results = await asyncio.gather(
            *(_provide(provider, user_text) for provider in providers)
        )
        blocks = [block for block in results if block]

    if blocks == agent.context_blocks:
        return
    agent.context_blocks = blocks
    await agent.llm._update_settings(
        LLMSettings(
            system_instruction=compose_system_instruction(agent.system_prompt, blocks)
        )
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
