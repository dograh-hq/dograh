"""Real-time feedback observer for sending pipeline events to the frontend.

This observer watches pipeline frames and sends relevant events (transcriptions,
bot text) over WebSocket to provide real-time feedback in the UI.

For frames with presentation timestamps (pts), like TTSTextFrame, we respect
the timing by queuing them and sending at the appropriate time, similar to
how base_output.py handles timed frames.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Set

from loguru import logger

if TYPE_CHECKING:
    from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    StopFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.time import nanoseconds_to_seconds


class RealtimeFeedbackObserver(BaseObserver):
    """Observer that sends real-time transcription and bot response events via WebSocket.

    For frames with pts (presentation timestamp), we queue them and send at the
    appropriate time to sync with audio playback.
    """

    def __init__(
        self,
        ws_sender: Callable[[dict], Awaitable[None]],
        logs_buffer: Optional["InMemoryLogsBuffer"] = None,
    ):
        """
        Args:
            ws_sender: Async function to send messages over WebSocket.
                       Expected signature: async def send(message: dict) -> None
            logs_buffer: Optional InMemoryLogsBuffer to persist events for post-call analysis.
        """
        super().__init__()
        self._ws_sender = ws_sender
        self._logs_buffer = logs_buffer
        self._frames_seen: Set[str] = set()

        # Clock/timing for pts-based frames (similar to base_output.py)
        self._clock_queue: Optional[asyncio.PriorityQueue] = None
        self._clock_task: Optional[asyncio.Task] = None
        self._clock_start_time: Optional[float] = (
            None  # Wall clock time when we started
        )
        self._pts_start_time: Optional[int] = None  # First pts value we saw

    async def _ensure_clock_task(self):
        """Create the clock task if it doesn't exist."""
        if self._clock_queue is None:
            self._clock_queue = asyncio.PriorityQueue()
            self._clock_task = asyncio.create_task(self._clock_task_handler())

    async def _cancel_clock_task(self):
        """Cancel the clock task and clear the queue.

        Called on interruption to discard any pending bot text that
        hasn't been sent yet.
        """
        if self._clock_task:
            self._clock_task.cancel()
            try:
                await self._clock_task
            except asyncio.CancelledError:
                pass
            self._clock_task = None
        self._clock_queue = None
        # Reset timing references so next bot response starts fresh
        self._clock_start_time = None
        self._pts_start_time = None

    async def _handle_interruption(self):
        """Handle interruption by clearing queued bot text.

        Similar to base_output.py's handle_interruptions, we cancel the
        clock task and recreate it to discard pending frames.
        """
        await self._cancel_clock_task()

    async def _clock_task_handler(self):
        """Process timed frames from the queue, respecting their presentation timestamps.

        Similar to base_output.py's _clock_task_handler, we wait until the
        frame's pts time has arrived before sending.
        """
        while True:
            try:
                pts, _frame_id, message = await self._clock_queue.get()

                # Calculate when to send based on pts relative to our start time
                if (
                    self._clock_start_time is not None
                    and self._pts_start_time is not None
                ):
                    # Target time = start wall time + (frame pts - start pts) in seconds
                    target_time = self._clock_start_time + nanoseconds_to_seconds(
                        pts - self._pts_start_time
                    )
                    current_time = time.time()
                    if target_time > current_time:
                        await asyncio.sleep(target_time - current_time)

                # Send the message
                await self._send_message(message)
                self._clock_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Clock task error: {e}")

    async def on_push_frame(self, data: FramePushed):
        """Process frames and send relevant ones to the client."""
        frame = data.frame
        frame_direction = data.direction

        # Handle pipeline termination - stop clock task
        if isinstance(frame, (EndFrame, CancelFrame, StopFrame)):
            await self._cancel_clock_task()
            return

        # Handle interruptions - clear any queued bot text
        if isinstance(frame, InterruptionFrame):
            await self._handle_interruption()
            return

        # Skip already processed frames (frames can be observed multiple times)
        if frame.id in self._frames_seen:
            return
        self._frames_seen.add(frame.id)

        # Handle user transcriptions (interim)
        if isinstance(frame, InterimTranscriptionFrame):
            await self._send_message(
                {
                    "type": "rtf-user-transcription",
                    "payload": {
                        "text": frame.text,
                        "final": False,
                        "user_id": frame.user_id,
                        "timestamp": frame.timestamp,
                    },
                }
            )
        # Handle user transcriptions (final)
        elif isinstance(frame, TranscriptionFrame):
            await self._send_message(
                {
                    "type": "rtf-user-transcription",
                    "payload": {
                        "text": frame.text,
                        "final": True,
                        "user_id": frame.user_id,
                        "timestamp": frame.timestamp,
                    },
                }
            )
            # Increment turn counter on final user transcription
            if self._logs_buffer:
                self._logs_buffer.increment_turn()
        # Handle bot TTS text - respect pts timing
        elif isinstance(frame, TTSTextFrame):
            message = {
                "type": "rtf-bot-text",
                "payload": {
                    "text": frame.text,
                },
            }

            # If frame has pts, queue it for timed delivery
            if frame.pts:
                # Initialize timing reference on first pts frame
                if self._pts_start_time is None:
                    self._pts_start_time = frame.pts
                    self._clock_start_time = time.time()

                await self._ensure_clock_task()
                await self._clock_queue.put((frame.pts, frame.id, message))
            else:
                # No pts, send immediately
                await self._send_message(message)
        # Handle function call in progress
        elif (
            isinstance(frame, FunctionCallInProgressFrame)
            and frame_direction == FrameDirection.DOWNSTREAM
        ):
            await self._send_message(
                {
                    "type": "rtf-function-call-start",
                    "payload": {
                        "function_name": frame.function_name,
                        "tool_call_id": frame.tool_call_id,
                    },
                }
            )
        # Handle function call result
        elif (
            isinstance(frame, FunctionCallResultFrame)
            and frame_direction == FrameDirection.DOWNSTREAM
        ):
            await self._send_message(
                {
                    "type": "rtf-function-call-end",
                    "payload": {
                        "function_name": frame.function_name,
                        "tool_call_id": frame.tool_call_id,
                        "result": str(frame.result) if frame.result else None,
                    },
                }
            )

    async def _send_message(self, message: dict):
        """Send message via WebSocket AND append to logs buffer, handling errors gracefully."""
        # Send via WebSocket
        try:
            await self._ws_sender(message)
        except Exception as e:
            # Log but don't fail - feedback is non-critical
            logger.debug(f"Failed to send real-time feedback message: {e}")

        # Also append to logs buffer
        if self._logs_buffer:
            try:
                await self._logs_buffer.append(message)
            except Exception as e:
                logger.error(f"Failed to append to logs buffer: {e}")
