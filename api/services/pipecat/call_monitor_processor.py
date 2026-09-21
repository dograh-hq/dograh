"""Monitor the call's duration and progress toward an agent response.

Call-worker heartbeats drive the duration check. Response deadlines are armed
before generation dispatch. Keeping both in the call pipeline preserves one
set of limits across agent visits.
"""

import time
from typing import Awaitable, Callable, Optional

from loguru import logger

from api.schemas.workflow_configurations import DEFAULT_MAX_CALL_DURATION_SECONDS
from api.services.pipecat.response_watchdog import CallResponseWatchdog
from pipecat.frames.frames import Frame, HeartbeatFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class CallMonitorProcessor(FrameProcessor):
    """Check call limits and arm response deadlines before generation dispatch.

    Lives after the user/answer gates and before the generation stage in both
    pipeline shapes. The shared watchdog also observes direct LLM requests and
    delivered playback; the engine owns hangup. The source callback returns
    None while ordinary inference is gated, such as during an agent transfer.
    """

    def __init__(
        self,
        *,
        response_watchdog: CallResponseWatchdog,
        response_source: Callable[[], FrameProcessor | None],
        max_call_duration_seconds: int = DEFAULT_MAX_CALL_DURATION_SECONDS,
        max_duration_end_task_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        super().__init__()
        self._response_watchdog = response_watchdog
        self._response_source = response_source
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

        if source := self._response_source():
            self._response_watchdog.before_inference(source, frame)

        await self.push_frame(frame, direction)

    async def cleanup(self):
        self._response_watchdog.close()
        await super().cleanup()

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
