"""Call-owned speech completion and muting, independent of bot activity events."""

import asyncio
import uuid
from collections.abc import Callable
from enum import Enum
from typing import Protocol

from loguru import logger

from pipecat.frames.frames import (
    AggregatedTextFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputAudioRawFrame,
    SpeechBoundaryFrame,
    StopFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.string import TextPartForConcatenation, concatenate_aggregated_text


class PlaybackOutcome(Enum):
    PLAYED = "played"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    CLOSED = "closed"


class SpeechPlaybackObserver(Protocol):
    """Delivery facts for call-owned consumers; observers supply their own policy."""

    def on_playback_expected(self, speech_id: str) -> None:
        """An explicit speech request was created, including direct recordings."""
        ...

    def on_speech_finished(self, speech_id: str, outcome: PlaybackOutcome) -> None:
        """An explicit speech request resolved, including failure/cancellation."""
        ...

    def on_response_expected(self, source: FrameProcessor) -> None:
        """A caller registered an expectation for the source's next response."""
        ...

    def on_response_started(self, source: FrameProcessor, speech_id: str) -> None:
        """The source emitted a response with this stable playback identity."""
        ...

    def on_output(self, speech_id: str) -> None:
        """Audio (or text in a text adapter) was delivered for this speech."""
        ...

    def on_playback_finished(self, speech_id: str) -> None:
        """The speech's end marker drained through the output queue."""
        ...

    def on_playback_cancelled(self, outcome: PlaybackOutcome) -> None:
        """Playback was invalidated by interruption or call closure."""
        ...


class SpeechPlayback:
    """One speech request; its deadline and mute outlive any individual waiter."""

    def __init__(
        self, owner: "SpeechPlaybackTracker", *, mute_user: bool, timeout: float
    ):
        self.id = str(uuid.uuid4())
        self.mute_user = mute_user
        self.has_output = False
        self.started = False
        self.text: str | None = None
        self.text_parts: list[TextPartForConcatenation] = []
        self._owner = owner
        self._result: asyncio.Future[PlaybackOutcome] = (
            asyncio.get_running_loop().create_future()
        )
        self._deadline = asyncio.get_running_loop().call_later(
            timeout, self.finish, PlaybackOutcome.TIMED_OUT
        )

    @property
    def done(self) -> bool:
        return self._result.done()

    @property
    def outcome(self) -> PlaybackOutcome | None:
        return self._result.result() if self.done else None

    async def wait(self) -> bool:
        """Return whether speech played; cancelling a waiter leaves playback owned."""
        return await asyncio.shield(self._result) is PlaybackOutcome.PLAYED

    def finish(self, outcome: PlaybackOutcome) -> None:
        if self.done:
            return
        self._deadline.cancel()
        self._owner.pending.pop(self.id, None)
        if outcome is PlaybackOutcome.TIMED_OUT:
            logger.warning(f"Speech {self.id} timed out; releasing its wait and mute")
        self._result.set_result(outcome)
        for observer in self._owner._observers:
            observer.on_speech_finished(self.id, outcome)
        if self is self._owner.greeting and self._owner.on_greeting_finished:
            self._owner.on_greeting_finished(self)


class SpeechPlaybackTracker:
    """Resolve speech at output boundaries and release every request's own mute."""

    def __init__(self):
        self.pending: dict[str, SpeechPlayback] = {}
        self.greeting: SpeechPlayback | None = None
        self.on_greeting_finished: Callable[[SpeechPlayback], None] | None = None
        self._output: FrameProcessor | None = None
        # Direct recordings can nest inside a still-generating TTS response.
        self._output_scopes: list[str] = []
        self._unmarked_response: str | None = None
        self._expected_response: SpeechPlayback | None = None
        self._expected_source: FrameProcessor | None = None
        self._response_sources: dict[FrameProcessor, str | None] = {}
        self._observers: list[SpeechPlaybackObserver] = []

    def add_observer(self, observer: SpeechPlaybackObserver) -> None:
        """Subscribe a call-owned consumer to delivery facts."""
        self._observers.append(observer)

    def observe_responses(self, source) -> None:
        """Tag generated responses so their delivered audio can be identified."""
        if isinstance(source, FrameProcessor):
            self._bind_response_boundaries(source)

    @property
    def mutes_user(self) -> bool:
        return any(speech.mute_user for speech in self.pending.values())

    @property
    def greeting_pending(self) -> bool:
        return self.greeting is not None and not self.greeting.done

    def create(
        self,
        *,
        mute_user: bool = False,
        timeout: float = 35,
        greeting: bool = False,
    ) -> SpeechPlayback:
        speech = SpeechPlayback(self, mute_user=mute_user, timeout=timeout)
        self.pending[speech.id] = speech
        if greeting:
            self.greeting = speech
        for observer in self._observers:
            observer.on_playback_expected(speech.id)
        return speech

    def expect_response(self, *, source=None, **kwargs) -> SpeechPlayback:
        """Own the next response emitted by the LLM, before requesting it.

        Tag both boundaries at the source so buffered responses retain ownership
        through timeout or interruption. Source-free adapters must deliver their
        response boundaries in order.
        """
        if self._expected_response and not self._expected_response.done:
            raise RuntimeError("A generated response is already pending")
        speech = self.create(**kwargs)
        self._expected_response = speech
        self._expected_source = source if isinstance(source, FrameProcessor) else None
        if self._expected_source:
            self._bind_response_boundaries(source)
            for observer in self._observers:
                observer.on_response_expected(source)
        return speech

    def _bind_response_boundaries(self, source: FrameProcessor) -> None:
        if source not in self._response_sources:
            self._response_sources[source] = None
            source.add_event_handler(
                "on_before_push_frame", self._mark_response_boundaries
            )

    def _mark_response_boundaries(self, source: FrameProcessor, frame: Frame) -> None:
        if isinstance(frame, LLMFullResponseStartFrame):
            speech = (
                self._expected_response if self._expected_source is source else None
            )
            # Unowned responses also need a scope so their audio cannot count as
            # output for another speech request whose boundaries overlap them.
            speech_id = speech.id if speech and not speech.done else str(uuid.uuid4())
            self._response_sources[source] = speech_id
            frame.metadata["dograh_speech_id"] = speech_id
            for observer in self._observers:
                observer.on_response_started(source, speech_id)
            if self._expected_source is source:
                self._expected_response = None
                self._expected_source = None
        elif isinstance(frame, LLMFullResponseEndFrame):
            frame.metadata["dograh_speech_id"] = self._response_sources[source]
            self._response_sources[source] = None

    def cancel_all(self, outcome: PlaybackOutcome = PlaybackOutcome.CLOSED) -> None:
        for observer in self._observers:
            observer.on_playback_cancelled(outcome)
        for speech in list(self.pending.values()):
            speech.finish(outcome)
        self._output_scopes.clear()
        self._unmarked_response = None
        self._expected_response = None
        self._expected_source = None
        # Keep source ownership through interruptions: an old generation's end
        # may still be emitted and must retain its old ID. Detach at call closure.
        if outcome is PlaybackOutcome.CLOSED:
            for source in self._response_sources:
                source.remove_event_handler(
                    "on_before_push_frame", self._mark_response_boundaries
                )
            self._response_sources.clear()

    def bind_output(self, output) -> None:
        if self._output:
            self._output.remove_event_handler(
                "on_before_process_frame", self.before_output
            )
            self._output.remove_event_handler("on_after_push_frame", self.after_output)
            self._output.remove_event_handler(
                "on_before_push_frame", self._capture_greeting_text
            )
        self._output = output if isinstance(output, FrameProcessor) else None
        if self._output:
            self._output.add_event_handler(
                "on_before_process_frame", self.before_output
            )
            self._output.add_event_handler("on_after_push_frame", self.after_output)
            self._output.add_event_handler(
                "on_before_push_frame", self._capture_greeting_text
            )

    def _capture_greeting_text(self, _processor, frame: Frame) -> None:
        """Keep generated greeting text out of context until playback succeeds."""
        speech = self.greeting
        if not (
            speech and speech.id in self._output_scopes and isinstance(frame, TextFrame)
        ):
            return
        if frame.append_to_context and speech.text is None:
            speech.text_parts.append(
                TextPartForConcatenation(
                    (
                        frame.raw_text
                        if isinstance(frame, AggregatedTextFrame) and frame.raw_text
                        else frame.text
                    ),
                    includes_inter_part_spaces=frame.includes_inter_frame_spaces,
                )
            )
        frame.append_to_context = False

    @staticmethod
    def greeting_text(speech: SpeechPlayback) -> str:
        return (
            speech.text
            if speech.text is not None
            else concatenate_aggregated_text(speech.text_parts)
        )

    async def before_output(self, processor, frame: Frame) -> None:
        # Suppressed interruptions never reach output. Resolve before transport
        # cancellation emits a bot-stop notification or discards queued markers.
        if isinstance(frame, InterruptionFrame):
            self.cancel_all(PlaybackOutcome.INTERRUPTED)
        elif isinstance(frame, (CancelFrame, StopFrame)):
            self.cancel_all()
        elif self._output is not None and processor is self._output:
            boundary = self._response_boundary(frame)
            if boundary:
                # Inject at this position in the transport's media queue. A
                # timestamped LLM end travels through a separate clock queue
                # and can overtake audio; the untimed boundary cannot. Processing
                # inline here also prevents a later response overtaking it in
                # the processor's input queue. The original frame is unchanged.
                boundary.transport_destination = frame.transport_destination
                await processor.process_frame(boundary, FrameDirection.DOWNSTREAM)

    def _response_boundary(self, frame: Frame) -> SpeechBoundaryFrame | None:
        if not isinstance(frame, (LLMFullResponseStartFrame, LLMFullResponseEndFrame)):
            return None
        if "dograh_speech_id" in frame.metadata:
            speech_id = frame.metadata["dograh_speech_id"]
            if speech_id:
                return SpeechBoundaryFrame(
                    speech_id, beginning=isinstance(frame, LLMFullResponseStartFrame)
                )
            return None

        # Only ordered adapters without a source use output-side pairing. Tagged
        # frames never replace this owner or claim a source-free expectation.
        if isinstance(frame, LLMFullResponseStartFrame):
            if (
                self._expected_source is None
                and self._expected_response
                and not self._expected_response.done
            ):
                self._unmarked_response = self._expected_response.id
                self._expected_response = None
                return SpeechBoundaryFrame(self._unmarked_response, beginning=True)
        elif self._unmarked_response:
            speech_id = self._unmarked_response
            self._unmarked_response = None
            return SpeechBoundaryFrame(speech_id, beginning=False)
        return None

    def after_output(self, _processor, frame: Frame) -> None:
        """Called only after the transport has written the preceding audio."""
        if isinstance(frame, SpeechBoundaryFrame):
            if frame.beginning:
                self._output_scopes.append(frame.speech_id)
                if speech := self.pending.get(frame.speech_id):
                    speech.started = True
            else:
                for observer in self._observers:
                    observer.on_playback_finished(frame.speech_id)
                speech = self.pending.get(frame.speech_id)
                if speech:
                    self._complete(speech)
                if frame.speech_id in self._output_scopes:
                    self._output_scopes.remove(frame.speech_id)
        elif self._output is None and isinstance(
            frame, (LLMFullResponseStartFrame, LLMFullResponseEndFrame)
        ):
            # Text chat delivers text synchronously and has no media queue.
            boundary = self._response_boundary(frame)
            if boundary:
                self.after_output(_processor, boundary)
        elif isinstance(frame, OutputAudioRawFrame) and frame.audio:
            self.note_output()
        elif isinstance(frame, EndFrame):
            self.cancel_all()

    def note_output(self) -> None:
        """Record delivered audio, or delivered text in the text-chat adapter."""
        speech = (
            self.pending.get(self._output_scopes[-1]) if self._output_scopes else None
        )
        if speech:
            speech.has_output = True
        if self._output_scopes:
            for observer in self._observers:
                observer.on_output(self._output_scopes[-1])

    @staticmethod
    def _complete(speech: SpeechPlayback) -> None:
        speech.finish(
            PlaybackOutcome.PLAYED if speech.has_output else PlaybackOutcome.FAILED
        )
