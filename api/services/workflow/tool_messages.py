"""Invocation-owned announcements, independent of tool work and result delivery."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import ValidationError

from api.schemas.tool import ToolMessageAudio, ToolMessages, ToolMessageVariants

if TYPE_CHECKING:
    from api.services.workflow.agent_runtime import AgentRuntime
    from api.services.workflow.pipecat_engine import PipecatEngine


def tool_messages(definition: dict[str, Any] | None) -> ToolMessages | None:
    """Read the shared plan, falling back to the original HTTP announcement."""
    definition = definition or {}
    raw = definition.get("messages")
    if raw is None and definition.get("type") == "http_api":
        config = definition.get("config") or {}
        if config.get("customMessageType") == "audio":
            if recording_id := config.get("customMessageRecordingId"):
                raw = {
                    "start": {
                        "variants": [{"type": "audio", "recording_id": recording_id}]
                    }
                }
        elif text := config.get("customMessage"):
            raw = {"start": {"variants": [{"type": "text", "text": text}]}}
    if raw is None:
        return None
    try:
        plan = ToolMessages.model_validate(raw)
    except ValidationError:
        # Old definitions can predate today's validation. A broken announcement
        # must not prevent the underlying tool from being registered or executed.
        logger.warning("Ignoring invalid tool message configuration")
        return None
    return plan if plan.start or plan.delayed else None


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
        self.plan = plan
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

    async def _speak(self, message: ToolMessageVariants):
        if not self.can_speak():
            return
        variant = random.choice(message.variants)
        source = (
            {"recording_pk": variant.recording_id}
            if isinstance(variant, ToolMessageAudio)
            else {"text": variant.text}
        )
        await self.engine.queue_speech(
            **source,
            mute_user=True,
            origin_agent=self.agent,
            can_queue=self.can_speak,
        )

    async def _run(self, started_at: float) -> None:
        try:
            if self.plan.start:
                await self._speak(self.plan.start)
            pending = list(self.plan.delayed)
            loop = asyncio.get_running_loop()
            while pending and self.can_speak():
                await asyncio.sleep(
                    max(0, started_at + pending[0].after_ms / 1000 - loop.time())
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
                while (
                    pending and started_at + pending[0].after_ms / 1000 <= loop.time()
                ):
                    message = pending.pop(0)
                await self._speak(message)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - isolate announcement failures from tool execution
            logger.exception("Tool announcement failed [{}]", self.tool_call_id)
