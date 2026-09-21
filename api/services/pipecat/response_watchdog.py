"""Call-owned response liveness, independent of playback completion and muting."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from api.services.pipecat.speech_playback import PlaybackOutcome
from pipecat.frames.frames import (
    Frame,
    FunctionCallCancelFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    LLMContextFrame,
    LLMMessagesAppendFrame,
    LLMMessagesTransformFrame,
    LLMMessagesUpdateFrame,
    LLMRunFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


@dataclass
class _ResponseWatch:
    """One owed response, including intervening tool generations."""

    source: FrameProcessor
    confirmed: bool
    tools: set[str] = field(default_factory=set)
    tool_deadline: float | None = None
    deadline: asyncio.TimerHandle | None = None
    expired: bool = False
    requests: set[int] = field(default_factory=set)
    awaiting_generation: bool = False
    awaiting_tool_response: bool = False
    latest_scope: str | None = None
    scopes: set[str] = field(default_factory=set)
    audible_scopes: set[str] = field(default_factory=set)


class CallResponseWatchdog:
    """Bound the time a call waits for its agent to respond.

    Normal responses get 35 seconds to deliver audio and renew that budget on
    each output write. Tools get a separate, fixed three-minute budget; their
    final result restores the audio deadline. Empty ends and recoverable
    errors leave it armed so provider recovery has time to finish.

    The engine owns this alongside its playback tracker and wires it up as a
    delivery observer. This controller owns response policy; the tracker owns
    delivery facts. Only the engine decides how to recover from expiry.
    """

    def __init__(
        self,
        *,
        on_timeout: Callable[[FrameProcessor], None],
        response_timeout: float = 35,
        tool_timeout: float = 180,
    ):
        self.response_timeout = response_timeout
        self.tool_timeout = tool_timeout
        self._on_timeout = on_timeout
        self._sources: dict[FrameProcessor, Callable[[], bool]] = {}
        self._response_watch: _ResponseWatch | None = None

    @property
    def pending_response(self) -> bool:
        return self._response_watch is not None

    def bind_source(self, source, *, enabled: Callable[[], bool]) -> None:
        """Observe inference requests and tools, using the engine's visit gate."""
        if not isinstance(source, FrameProcessor):
            return
        if source not in self._sources:
            source.add_event_handler("on_before_process_frame", self.before_inference)
            source.add_event_handler("on_before_push_frame", self._watch_tool)
        self._sources[source] = enabled

    def before_inference(self, source: FrameProcessor, frame: Frame) -> None:
        """Observe requests at the call monitor and at the LLM's own input."""
        enabled = self._sources.get(source)
        if not enabled or not enabled():
            return
        if isinstance(
            frame, (LLMContextFrame, LLMRunFrame, UserStoppedSpeakingFrame)
        ) or (
            isinstance(
                frame,
                (
                    LLMMessagesAppendFrame,
                    LLMMessagesUpdateFrame,
                    LLMMessagesTransformFrame,
                ),
            )
            and frame.run_llm
        ):
            speculative = isinstance(frame, LLMContextFrame) and frame.speculation
            watch = self._response_watch
            if watch is None or watch.source is not source:
                self.cancel()
                watch = _ResponseWatch(source=source, confirmed=not speculative)
                self._response_watch = watch
                self._renew_deadline(watch)
            elif not speculative and not watch.confirmed:
                watch.confirmed = True
                self._renew_deadline(watch)
            if (
                not isinstance(frame, UserStoppedSpeakingFrame)
                and frame.id not in watch.requests
            ):
                watch.requests.add(frame.id)
                watch.awaiting_generation = True
            # Repeated requests/retries without audio must not extend silence.

    def cancel(self) -> None:
        """Invalidate the current response on interruption or handoff."""
        if self._response_watch and self._response_watch.deadline:
            self._response_watch.deadline.cancel()
        self._response_watch = None

    def close(self) -> None:
        """Release the deadline and subscriptions at call closure."""
        self.cancel()
        for source in self._sources:
            source.remove_event_handler(
                "on_before_process_frame", self.before_inference
            )
            source.remove_event_handler("on_before_push_frame", self._watch_tool)
        self._sources.clear()

    def response_timed_out(self, source: FrameProcessor) -> bool:
        watch = self._response_watch
        return bool(watch and watch.source is source and watch.expired)

    def _renew_deadline(self, watch: _ResponseWatch) -> None:
        if not watch.confirmed or watch.expired:
            return
        if watch.deadline:
            watch.deadline.cancel()
        loop = asyncio.get_running_loop()
        deadline = watch.tool_deadline or (loop.time() + self.response_timeout)
        watch.deadline = loop.call_at(deadline, self._response_expired, watch)

    def _response_expired(self, watch: _ResponseWatch) -> None:
        if self._response_watch is not watch:
            return
        watch.expired = True
        self._on_timeout(watch.source)

    def _watch_tool(self, source: FrameProcessor, frame: Frame) -> None:
        watch = self._response_watch
        if not watch or watch.source is not source:
            return
        if isinstance(frame, FunctionCallsStartedFrame):
            new_ids = {call.tool_call_id for call in frame.function_calls} - watch.tools
            if new_ids:
                if not watch.tools:
                    watch.tool_deadline = (
                        asyncio.get_running_loop().time() + self.tool_timeout
                    )
                watch.tools.update(new_ids)
                watch.awaiting_tool_response = True
                self._renew_deadline(watch)
        elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            if (
                isinstance(frame, FunctionCallResultFrame)
                and frame.properties
                and not frame.properties.is_final
            ):
                return
            if frame.tool_call_id in watch.tools:
                watch.tools.remove(frame.tool_call_id)
                if not watch.tools:
                    watch.tool_deadline = None
                    self._renew_deadline(watch)

    def on_response_expected(self, source: FrameProcessor) -> None:
        self.before_inference(source, LLMRunFrame())

    def on_response_started(self, source: FrameProcessor, speech_id: str) -> None:
        watch = self._response_watch
        if watch and watch.source is source:
            watch.scopes.add(speech_id)
            watch.latest_scope = speech_id
            watch.awaiting_generation = False
            if not watch.tools:
                watch.awaiting_tool_response = False

    def on_output(self, speech_id: str) -> None:
        watch = self._response_watch
        if watch and speech_id in watch.scopes:
            watch.audible_scopes.add(speech_id)
            self._renew_deadline(watch)

    def on_playback_finished(self, speech_id: str) -> None:
        watch = self._response_watch
        if not watch or speech_id not in watch.scopes:
            return
        if (
            speech_id in watch.audible_scopes
            and not watch.tools
            and not watch.awaiting_generation
            and not watch.awaiting_tool_response
            and watch.latest_scope == speech_id
        ):
            self.cancel()
        # Empty and tool-only generations keep the deadline until their
        # continuation actually reaches output.
        watch.scopes.discard(speech_id)
        watch.audible_scopes.discard(speech_id)

    def on_playback_cancelled(self, outcome: PlaybackOutcome) -> None:
        if outcome is PlaybackOutcome.CLOSED:
            self.close()
        else:
            self.cancel()
