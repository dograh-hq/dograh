import time
from typing import Awaitable, Callable, Optional

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    HeartbeatFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class PipelineEngineCallbacksProcessor(FrameProcessor):
    """
    Custom PipelineEngineCallbacksProcessor that accepts callbacks for various
    use cases, like ending tasks when max call duration is exceeded, or informing
    the engine that the bot is done speaking.
    """

    def __init__(
        self,
        max_call_duration_seconds: int = 300,
        max_duration_end_task_callback: Optional[Callable[[], Awaitable[None]]] = None,
        generation_started_callback: Optional[Callable[[], Awaitable[None]]] = None,
        llm_text_frame_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        super().__init__()
        self._start_time = None
        self._max_call_duration_seconds = max_call_duration_seconds
        self._max_duration_end_task_callback = max_duration_end_task_callback
        self._generation_started_callback = generation_started_callback
        self._llm_text_frame_callback = llm_text_frame_callback
        self._end_task_frame_pushed = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self._start(frame)
        elif isinstance(frame, HeartbeatFrame):
            await self._check_call_duration()
        elif isinstance(frame, LLMFullResponseStartFrame):
            await self._generation_started()
        elif (
            isinstance(frame, (LLMTextFrame, TTSSpeakFrame))
            and self._llm_text_frame_callback
        ):
            # Include TTSSpeakFrame here since for static nodes, we send TTSSpeakFrame
            # which can act as reference while fixing the aggregated trascript
            await self._llm_text_frame_callback(frame.text)
        await self.push_frame(frame, direction)

    async def _start(self, _: StartFrame):
        self._start_time = time.time()

    async def _check_call_duration(self):
        if self._start_time is not None:
            if time.time() - self._start_time > self._max_call_duration_seconds:
                if not self._end_task_frame_pushed:
                    if self._max_duration_end_task_callback:
                        await self._max_duration_end_task_callback()
                    self._end_task_frame_pushed = True
                else:
                    logger.debug(
                        "Max call duration exceeded. Skipping EndTaskFrame since already sent"
                    )

    async def _generation_started(self):
        if self._generation_started_callback:
            await self._generation_started_callback()
