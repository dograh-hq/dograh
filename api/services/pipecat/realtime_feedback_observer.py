"""Real-time feedback observer for sending pipeline events to the frontend.

This observer watches pipeline frames and sends relevant events (transcriptions,
bot text, function calls, TTFB metrics) over WebSocket to provide real-time
feedback in the UI.

For TTS text, we wait until the frame has passed through BaseOutputTransport.
That transport already applies presentation timestamp timing against audio
playback, so the UI text is emitted from the same clock as the spoken audio.

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

import json
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Set

from loguru import logger

from api.services.pipecat.realtime_feedback_events import (
    build_bot_text_event,
    build_function_call_end_event,
    build_function_call_start_event,
    build_pipeline_error_event,
    build_ttfb_metric_event,
    build_user_transcription_event,
)

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
    TTSSpeakFrame,
    TTSTextFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.utils.enums import RealtimeFeedbackType


class RealtimeFeedbackObserver(BaseObserver):
    """Observer that sends real-time events via WebSocket and persists final transcripts.

    WebSocket streaming (all events for live UI):
    - User transcriptions (interim and final)
    - Bot TTS text after output transport timing
    - Function calls (start/end)
    - TTFB metrics (LLM generation time only)

    Logs buffer persistence (only final data for post-call analysis):
    - Complete user transcripts per turn (via on_user_turn_stopped)
    - Complete assistant transcripts per turn (via on_assistant_turn_stopped)
    - Function calls and TTFB metrics

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

    async def cleanup(self):
        """Clean up resources. Must be called when the observer is no longer needed."""
        pass

    async def on_push_frame(self, data: FramePushed):
        """Process frames and send relevant ones to the client."""
        frame = data.frame
        frame_direction = data.direction
        source = data.source

        # Skip already processed frames (frames can be observed multiple times).
        # ErrorFrames are accepted in either direction — push_error() emits them
        # UPSTREAM, and we still want to surface them to the UI. Upstream-only
        # transcription frames are accepted too: upstream Gemini Live emits user
        # transcripts toward the user aggregator, not downstream. Broadcast
        # transcription siblings are still handled only on the downstream copy to
        # avoid duplicate live UI messages.
        if frame.id in self._frames_seen:
            return
        if frame_direction != FrameDirection.DOWNSTREAM:
            is_upstream_transcription = (
                isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
                and frame.broadcast_sibling_id is None
            )
            if not isinstance(frame, ErrorFrame) and not is_upstream_transcription:
                return

        # TTSTextFrame may be observed before the output transport has applied
        # its audio clock. Match RTVIObserver: leave the frame unmarked so the
        # transport-pushed copy can be handled with playback timing already done.
        if isinstance(frame, TTSTextFrame) and not isinstance(
            source, BaseOutputTransport
        ):
            return

        self._frames_seen.add(frame.id)

        logger.trace(f"{self} Received Frame: {frame} Direction: {frame_direction}")

        if isinstance(frame, (EndFrame, CancelFrame, StopFrame, InterruptionFrame)):
            return
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
                build_user_transcription_event(
                    text=frame.text,
                    final=False,
                    user_id=frame.user_id,
                    timestamp=frame.timestamp,
                )
            )
        # Handle user transcriptions (final) - WebSocket only
        # Complete turn text is persisted via register_turn_handlers
        elif isinstance(frame, TranscriptionFrame):
            await self._send_ws(
                build_user_transcription_event(
                    text=frame.text,
                    final=True,
                    user_id=frame.user_id,
                    timestamp=frame.timestamp,
                )
            )
        # Handle engine-queued speech (transition/tool messages) marked for
        # log persistence. The downstream TTSTextFrame(s) from the TTS service
        # still stream to WS as normal; we persist the full utterance once here
        # to avoid word-level log entries from word-timestamp providers.
        elif isinstance(frame, TTSSpeakFrame):
            if getattr(frame, "persist_to_logs", False):
                await self._append_to_buffer(build_bot_text_event(text=frame.text))
        # Handle bot TTS text after output transport timing, WebSocket only
        # Complete turn text is persisted via register_turn_handlers,
        # except for frames explicitly flagged persist_to_logs (e.g. recording
        # transcripts from play_audio) which bypass the aggregator path.
        elif isinstance(frame, TTSTextFrame):
            message = build_bot_text_event(text=frame.text)

            if getattr(frame, "persist_to_logs", False):
                await self._send_message(message)
            else:
                await self._send_ws(message)
        # Handle function call in progress
        elif (
            isinstance(frame, FunctionCallInProgressFrame)
            and frame_direction == FrameDirection.DOWNSTREAM
        ):
            await self._send_message(
                build_function_call_start_event(
                    function_name=frame.function_name,
                    tool_call_id=frame.tool_call_id,
                    arguments=dict(frame.arguments or {}),
                )
            )
        # Handle function call result
        elif (
            isinstance(frame, FunctionCallResultFrame)
            and frame_direction == FrameDirection.DOWNSTREAM
        ):
            await self._send_message(
                build_function_call_end_event(
                    function_name=frame.function_name,
                    tool_call_id=frame.tool_call_id,
                    result=frame.result,
                )
            )
        # Handle TTFB metrics - capture LLM generation time only
        elif isinstance(frame, MetricsFrame):
            # Check if this MetricsFrame contains TTFB data from an LLM processor
            for metric_data in frame.data:
                if isinstance(metric_data, TTFBMetricsData):
                    # Only send TTFB if it's from an LLM processor
                    if metric_data.processor and "LLM" in metric_data.processor:
                        await self._send_message(
                            build_ttfb_metric_event(
                                ttfb_seconds=metric_data.value,
                                processor=metric_data.processor,
                                model=metric_data.model,
                            )
                        )
        # Handle pipeline errors
        elif isinstance(frame, ErrorFrame):
            processor_name = str(frame.processor) if frame.processor else None
            extra_payload: dict[str, object] = {}
            # Surface structured fields when the underlying exception carries
            # them (e.g. google.genai APIError: code=1008, status=None,
            # message="Your project has been denied access...").
            exc = frame.exception
            if exc is not None:
                exc_type = type(exc).__name__
                extra_payload["exception_type"] = exc_type
                extra_payload["exception_message"] = str(exc)
                for attr in ("code", "status", "message", "details"):
                    value = getattr(exc, attr, None)
                    if value is None or attr in extra_payload:
                        continue
                    try:
                        # Ensure the value is JSON-serializable; fall back
                        # to str() for opaque objects (e.g. raw response).
                        json.dumps(value)
                        extra_payload[attr] = value
                    except (TypeError, ValueError):
                        extra_payload[attr] = str(value)
            await self._send_message(
                build_pipeline_error_event(
                    error=frame.error,
                    fatal=frame.fatal,
                    processor=processor_name,
                    extra_payload=extra_payload or None,
                )
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
                build_user_transcription_event(
                    text=message.content,
                    final=True,
                    timestamp=message.timestamp,
                )
            )
        except Exception as e:
            logger.error(f"Failed to append user turn to logs buffer: {e}")

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        if message.content:
            try:
                await logs_buffer.append(
                    build_bot_text_event(
                        text=message.content,
                        timestamp=message.timestamp,
                    )
                )
            except Exception as e:
                logger.error(f"Failed to append assistant turn to logs buffer: {e}")
