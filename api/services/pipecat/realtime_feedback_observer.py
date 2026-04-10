"""Real-time feedback observer for sending pipeline events to the frontend.

This observer watches pipeline frames and sends relevant events (transcriptions,
bot text, function calls, TTFB metrics) over WebSocket to provide real-time
feedback in the UI.

For frames with presentation timestamps (pts), like TTSTextFrame, we respect
the timing by queuing them and sending at the appropriate time, similar to
how base_output.py handles timed frames.

Streaming vs. persisted data:
- WebSocket receives all events in real-time (interim transcriptions, TTS text
  chunks, function calls, metrics) for live UI feedback.
- The logs buffer only stores final complete transcripts per turn (via
  register_turn_handlers hooking into aggregator events), function calls,
  and metrics — not interim/streaming data.

Note: Node transition events are sent directly from PipecatEngine.set_node()
rather than being observed here, to ensure precise timing at the moment of
node changes.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Set

from loguru import logger

if TYPE_CHECKING:
    from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    ErrorFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    MetricsFrame,
    StopFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.enums import RealtimeFeedbackType
from pipecat.utils.time import nanoseconds_to_seconds


class RealtimeFeedbackObserver(BaseObserver):
    """Observer that sends real-time events via WebSocket and persists final transcripts.

    WebSocket streaming (all events for live UI):
    - User transcriptions (interim and final)
    - Bot TTS text (with pts-based timing)
    - Function calls (start/end)
    - TTFB metrics (LLM generation time only)

    Logs buffer persistence (only final data for post-call analysis):
    - Complete user transcripts per turn (via on_user_turn_stopped)
    - Complete assistant transcripts per turn (via on_assistant_turn_stopped)
    - Function calls and TTFB metrics

    For frames with pts (presentation timestamp), we queue them and send at the
    appropriate time to sync with audio playback.

    Note: Node transitions are handled by PipecatEngine.set_node() callback.
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

    async def cleanup(self):
        """Clean up resources. Must be called when the observer is no longer needed."""
        await self._cancel_clock_task()

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

                # Send the message (clock queue only has TTS text, WS-only)
                await self._send_ws(message)
                self._clock_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Clock task error: {e}")

    async def on_push_frame(self, data: FramePushed):
        """Process frames and send relevant ones to the client."""
        frame = data.frame
        frame_direction = data.direction

        # Skip already processed frames (frames can be observed multiple times)
        if (
            frame.id in self._frames_seen
            or frame_direction != FrameDirection.DOWNSTREAM
        ):
            return
        self._frames_seen.add(frame.id)

        logger.trace(f"{self} Received Frame: {frame} Direction: {frame_direction}")

        # Handle pipeline termination - stop clock task
        if isinstance(frame, (EndFrame, CancelFrame, StopFrame)):
            await self._cancel_clock_task()
        # Handle interruptions - clear any queued bot text
        elif isinstance(frame, InterruptionFrame):
            await self._handle_interruption()
        # Bot speaking state - WS only (ephemeral state signals, not persisted)
        elif isinstance(frame, BotStartedSpeakingFrame):
            await self._send_ws(
                {"type": RealtimeFeedbackType.BOT_STARTED_SPEAKING.value, "payload": {}}
            )
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._send_ws(
                {"type": RealtimeFeedbackType.BOT_STOPPED_SPEAKING.value, "payload": {}}
            )
        # User mute state - WS only (ephemeral state signals, not persisted)
        elif isinstance(frame, UserMuteStartedFrame):
            await self._send_ws(
                {"type": RealtimeFeedbackType.USER_MUTE_STARTED.value, "payload": {}}
            )
        elif isinstance(frame, UserMuteStoppedFrame):
            await self._send_ws(
                {"type": RealtimeFeedbackType.USER_MUTE_STOPPED.value, "payload": {}}
            )
        # Handle user transcriptions (interim) - WebSocket only
        elif isinstance(frame, InterimTranscriptionFrame):
            await self._send_ws(
                {
                    "type": RealtimeFeedbackType.USER_TRANSCRIPTION.value,
                    "payload": {
                        "text": frame.text,
                        "final": False,
                        "user_id": frame.user_id,
                        "timestamp": frame.timestamp,
                    },
                }
            )
        # Handle user transcriptions (final) - WebSocket only
        # Complete turn text is persisted via register_turn_handlers
        elif isinstance(frame, TranscriptionFrame):
            await self._send_ws(
                {
                    "type": RealtimeFeedbackType.USER_TRANSCRIPTION.value,
                    "payload": {
                        "text": frame.text,
                        "final": True,
                        "user_id": frame.user_id,
                        "timestamp": frame.timestamp,
                    },
                }
            )
        # Handle bot TTS text - respect pts timing, WebSocket only
        # Complete turn text is persisted via register_turn_handlers
        elif isinstance(frame, TTSTextFrame):
            message = {
                "type": RealtimeFeedbackType.BOT_TEXT.value,
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
                await self._send_ws(message)
        # Handle function call in progress
        elif (
            isinstance(frame, FunctionCallInProgressFrame)
            and frame_direction == FrameDirection.DOWNSTREAM
        ):
            await self._send_message(
                {
                    "type": RealtimeFeedbackType.FUNCTION_CALL_START.value,
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
                    "type": RealtimeFeedbackType.FUNCTION_CALL_END.value,
                    "payload": {
                        "function_name": frame.function_name,
                        "tool_call_id": frame.tool_call_id,
                        "result": str(frame.result) if frame.result else None,
                    },
                }
            )
        # Handle TTFB metrics - capture LLM generation time only
        elif isinstance(frame, MetricsFrame):
            # Check if this MetricsFrame contains TTFB data from an LLM processor
            for metric_data in frame.data:
                if isinstance(metric_data, TTFBMetricsData):
                    # Only send TTFB if it's from an LLM processor
                    if metric_data.processor and "LLM" in metric_data.processor:
                        await self._send_message(
                            {
                                "type": RealtimeFeedbackType.TTFB_METRIC.value,
                                "payload": {
                                    "ttfb_seconds": metric_data.value,
                                    "processor": metric_data.processor,
                                    "model": metric_data.model,
                                },
                            }
                        )
        # Handle pipeline errors
        elif isinstance(frame, ErrorFrame):
            processor_name = str(frame.processor) if frame.processor else None
            await self._send_message(
                {
                    "type": RealtimeFeedbackType.PIPELINE_ERROR.value,
                    "payload": {
                        "error": frame.error,
                        "fatal": frame.fatal,
                        "processor": processor_name,
                    },
                }
            )

    async def _send_ws(self, message: dict):
        """Send message via WebSocket only, handling errors gracefully."""
        if not self._ws_sender:
            return
        try:
            # Inject current node info from the logs buffer
            if self._logs_buffer and self._logs_buffer.current_node_id:
                message = {
                    **message,
                    "node_id": self._logs_buffer.current_node_id,
                    "node_name": self._logs_buffer.current_node_name,
                }
            await self._ws_sender(message)
        except Exception as e:
            logger.debug(f"Failed to send real-time feedback message: {e}")

    async def _send_message(self, message: dict):
        """Send message via WebSocket AND append to logs buffer."""
        await self._send_ws(message)
        await self._append_to_buffer(message)

    async def _append_to_buffer(self, message: dict):
        """Append message to logs buffer, handling errors gracefully."""
        if self._logs_buffer:
            try:
                await self._logs_buffer.append(message)
            except Exception as e:
                logger.error(f"Failed to append to logs buffer: {e}")


def register_turn_log_handlers(
    logs_buffer: "InMemoryLogsBuffer",
    user_aggregator,
    assistant_aggregator,
):
    """Register event handlers on aggregators to persist final turn transcripts.

    Hooks into on_user_turn_stopped and on_assistant_turn_stopped to store
    complete turn text in the logs buffer. Works for both WebRTC and telephony
    calls — independent of WebSocket availability.
    """

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        logs_buffer.increment_turn()
        try:
            await logs_buffer.append(
                {
                    "type": RealtimeFeedbackType.USER_TRANSCRIPTION.value,
                    "payload": {
                        "text": message.content,
                        "final": True,
                        "timestamp": message.timestamp,
                    },
                }
            )
        except Exception as e:
            logger.error(f"Failed to append user turn to logs buffer: {e}")

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        if message.content:
            try:
                await logs_buffer.append(
                    {
                        "type": RealtimeFeedbackType.BOT_TEXT.value,
                        "payload": {
                            "text": message.content,
                            "timestamp": message.timestamp,
                        },
                    }
                )
            except Exception as e:
                logger.error(f"Failed to append assistant turn to logs buffer: {e}")
