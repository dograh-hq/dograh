"""The call's own clock.

Belongs to the call pipeline and nowhere else. Heartbeats only reach the
worker that emits them, and agent workers run with heartbeats disabled, so a
copy of this inside an agent's generation stage would never fire -- and if it
did, each agent would be ending the call on a timer of its own.
"""

import time
from typing import Awaitable, Callable, Optional

from loguru import logger

from api.schemas.workflow_configurations import DEFAULT_MAX_CALL_DURATION_SECONDS
from pipecat.frames.frames import Frame, HeartbeatFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class CallDurationProcessor(FrameProcessor):
    """End the call once it has run past its configured maximum duration."""

    def __init__(
        self,
        max_call_duration_seconds: int = DEFAULT_MAX_CALL_DURATION_SECONDS,
        max_duration_end_task_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        super().__init__()
        self._start_time = None
        self._max_call_duration_seconds = max_call_duration_seconds
        self._max_duration_end_task_callback = max_duration_end_task_callback
        self._end_task_frame_pushed = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._start_time = time.time()
        elif isinstance(frame, HeartbeatFrame):
            await self._check_call_duration()

        await self.push_frame(frame, direction)

    async def _check_call_duration(self):
        if self._start_time is None:
            return
        if time.time() - self._start_time <= self._max_call_duration_seconds:
            return
        if self._end_task_frame_pushed:
            logger.debug(
                "Max call duration exceeded. Skipping termination since already requested"
            )
            return
        if self._max_duration_end_task_callback:
            await self._max_duration_end_task_callback()
        self._end_task_frame_pushed = True
