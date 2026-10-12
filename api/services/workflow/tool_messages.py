"""Invocation-owned announcements, independent of tool work and result delivery."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import TypeAdapter, ValidationError

from api.schemas.tool import ToolMessage, ToolMessages

if TYPE_CHECKING:
    from api.services.workflow.agent_runtime import AgentRuntime
    from api.services.workflow.pipecat_engine import PipecatEngine


_messages_adapter = TypeAdapter(ToolMessages)


def tool_messages(definition: dict[str, Any] | None) -> ToolMessages:
    """Read the canonical message list. Legacy data is handled by Alembic."""
    try:
        return _messages_adapter.validate_python((definition or {}).get("messages", []))
    except ValidationError:
        logger.warning("Ignoring invalid tool message configuration")
        return []


def first_start_message(definition: dict[str, Any] | None) -> ToolMessage | None:
    """Control tools support one start message, with no variant selection."""
    return next(
        (message for message in tool_messages(definition) if message.event == "start"),
        None,
    )


def speech_source(message: ToolMessage) -> dict[str, Any]:
    return (
        {"recording_pk": message.recording_id}
        if message.type == "audio"
        else {"text": message.text}
    )


class ToolMessageSession:
    """One selected invocation supplies announcements for its concurrent group.

    Cancellation stops speech preparation and future updates, never the request.
    Delayed messages remain here until the speech lane is available, so updates
    that become obsolete while waiting are not placed in the media queue.
    """

    def __init__(
        self,
        engine: PipecatEngine,
        agent: AgentRuntime,
        tool_call_id: str,
        plan: ToolMessages,
    ):
        self.engine = engine
        self.agent = agent
        self.tool_call_id = tool_call_id
        self.start_variants = [message for message in plan if message.event == "start"]
        self.delayed_variants: dict[int, ToolMessages] = {}
        for message in plan:
            if message.event == "delayed":
                self.delayed_variants.setdefault(message.after_ms, []).append(message)
        self.stopped = False
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(
            self._run(asyncio.get_running_loop().time()),
            name=f"tool-messages:{self.agent.visit_id}:{self.tool_call_id}",
        )

    def stop(self) -> None:
        self.stopped = True
        if self.task and not self.task.done():
            self.task.cancel()

    def can_speak(self) -> bool:
        return not self.stopped and self.engine.agent_can_act(self.agent)

    async def _speak(self, variants: ToolMessages):
        if not self.can_speak():
            return
        await self.engine.queue_speech(
            **speech_source(random.choice(variants)),
            mute_user=True,
            origin_agent=self.agent,
            can_queue=self.can_speak,
        )

    async def _run(self, started_at: float) -> None:
        try:
            if self.start_variants:
                await self._speak(self.start_variants)
            pending = sorted(self.delayed_variants)
            loop = asyncio.get_running_loop()
            while pending and self.can_speak():
                await asyncio.sleep(
                    max(0, started_at + pending[0] / 1000 - loop.time())
                )
                # Keep fillers outside the media queue while prior speech plays.
                # This also covers a start recording that is still being voiced.
                while self.can_speak() and (
                    self.engine.speech_playback.pending or self.engine._bot_is_speaking
                ):
                    await asyncio.sleep(0.05)
                if not self.can_speak():
                    return
                message = pending.pop(0)
                # If several thresholds elapsed during speech, only the latest
                # update remains useful. Never read a backlog of waiting lines.
                while pending and started_at + pending[0] / 1000 <= loop.time():
                    message = pending.pop(0)
                await self._speak(self.delayed_variants[message])
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - isolate announcement failures from tool execution
            logger.exception("Tool announcement failed [{}]", self.tool_call_id)
