"""Pipecat bridge that prepares CALM context immediately before the LLM."""

from collections.abc import Awaitable, Callable

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class CalmPromptProcessor(FrameProcessor):
    """Run a prompt callback before each Sakinah context reaches the LLM."""

    def __init__(self, prepare_prompt: Callable[[object], Awaitable[None]]):
        super().__init__()
        self._prepare_prompt = prepare_prompt

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, LLMContextFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            await self._prepare_prompt(frame.context)
        await self.push_frame(frame, direction)
