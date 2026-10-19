"""Call-owned speech completion and muting, independent of bot activity events."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from loguru import logger

from api.services.pipecat.recording_word_timings import recording_prefix
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputAudioRawFrame,
    SpeechBoundaryFrame,
    SpeechOutputAudioRawFrame,
    StopFrame,
    TextFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
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
        self.allow_interrupt = True
        self.interrupt_min_words = 2
        # Generated realtime text remains owned by the provider aggregator.
        self.manage_text = True
        self.text: str | None = None
        self.text_parts: list[TextPartForConcatenation] = []
        self._text_ends: list[float] = []
        self._word_origin: float | None = None
        self.context: LLMContext | None = None
        self.delivered_text = ""
        self.played_seconds = 0.0
        self.queued_seconds = 0.0
        self.recording_duration: float | None = None
        self.timing_source = "unavailable"
        self.first_output_at: str | None = None
        self.requested_at = asyncio.get_running_loop().time()
        self.connection_started_at: float | None = None
        self.finished_at: str | None = None
        self.log_context: dict = {}
        self._pending_word: TextPartForConcatenation | None = None
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
        self.finished_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        self.delivered_text = self._delivered_text(outcome)
        self._result.set_result(outcome)
        for observer in self._owner._observers:
            observer.on_speech_finished(self.id, outcome)
        if self is self._owner.greeting and self._owner.on_greeting_finished:
            self._owner.on_greeting_finished(self)

    def _delivered_text(self, outcome: PlaybackOutcome) -> str:
        if not self.has_output:
            return ""
        if self.recording_duration is not None:
            return recording_prefix(
                self.text or "", self.recording_duration, self.played_seconds
            )
        complete = (
            outcome is PlaybackOutcome.PLAYED
            and self.played_seconds + 1e-6 >= self.queued_seconds
        )
        if complete and self.text is not None:
            return self.text
        parts = []
        for part, end in zip(self.text_parts, self._text_ends):
            if end > self.played_seconds + 1e-6:
                break
            parts.append(part)
        if complete and self._pending_word is not None:
            parts.append(self._pending_word)
        return concatenate_aggregated_text(parts)


class SpeechPlaybackTracker:
    """Resolve speech at output boundaries and release every request's own mute."""

    def __init__(self):
        self.pending: dict[str, SpeechPlayback] = {}
        self.greeting: SpeechPlayback | None = None
        self.on_greeting_finished: Callable[[SpeechPlayback], None] | None = None
        self.on_greeting_started: Callable[[SpeechPlayback], None] | None = None
        self._output: FrameProcessor | None = None
        # Direct recordings can nest inside a still-generating TTS response.
        self._output_scopes: list[str] = []
        self._input_scopes: list[str] = []
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
        self._input_scopes.clear()
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
        """Own greeting text until its delivery result can be committed once."""
        speech = self.greeting
        if speech is not None and not speech.manage_text:
            return
        if isinstance(frame, TextFrame) and frame.metadata.get("dograh_greeting_id"):
            frame.append_to_context = False
            if isinstance(frame, TTSTextFrame):
                frame.persist_to_logs = False
            if speech is None or frame.metadata["dograh_greeting_id"] != speech.id:
                return
        if not (
            speech
            and isinstance(frame, TextFrame)
            and (
                speech.id in self._output_scopes
                or frame.metadata.get("dograh_greeting_id") == speech.id
            )
        ):
            return
        if not speech.done and isinstance(frame, TTSTextFrame):
            frame.persist_to_logs = False
            text = frame.raw_text or frame.text
            if frame.aggregated_by == "recording_router":
                from api.services.workflow.pipecat_engine_context_composer import (
                    RECORDING_MARKER,
                )

                recording_parts = (
                    text.lstrip().removeprefix(RECORDING_MARKER).split(maxsplit=1)
                )
                text = recording_parts[1] if len(recording_parts) == 2 else ""
            part = TextPartForConcatenation(
                text,
                includes_inter_part_spaces=frame.includes_inter_frame_spaces,
            )
            if frame.pts is not None or frame.aggregated_by == "word":
                speech.timing_source = "provider"
                word_end = speech.played_seconds
                if frame.pts is not None:
                    if speech._word_origin is None:
                        speech._word_origin = frame.pts / 1e9 - speech.played_seconds
                    word_end = frame.pts / 1e9 - speech._word_origin
                if speech._pending_word is not None:
                    speech.text_parts.append(speech._pending_word)
                    speech._text_ends.append(word_end)
                speech._pending_word = part
            elif frame.aggregated_by != "recording" and speech.has_output:
                speech.timing_source = "segment"
                if speech._pending_word is not None:
                    speech.text_parts.append(speech._pending_word)
                    speech._text_ends.append(speech.played_seconds)
                    speech._pending_word = None
                speech.text_parts.append(part)
                speech._text_ends.append(speech.played_seconds)
        frame.append_to_context = False

    @staticmethod
    def greeting_text(speech: SpeechPlayback) -> str:
        return (
            speech.delivered_text
            if speech.done
            else speech._delivered_text(PlaybackOutcome.INTERRUPTED)
        )

    async def before_output(self, processor, frame: Frame) -> None:
        # Suppressed interruptions never reach output. Resolve before transport
        # cancellation emits a bot-stop notification or discards queued markers.
        if isinstance(frame, InterruptionFrame):
            self.cancel_all(PlaybackOutcome.INTERRUPTED)
        elif isinstance(frame, (CancelFrame, StopFrame)):
            self.cancel_all()
        elif self._output is not None and processor is self._output:
            boundary = (
                frame
                if isinstance(frame, SpeechBoundaryFrame)
                else self._response_boundary(frame)
            )
            if boundary:
                if boundary.beginning:
                    self._input_scopes.append(boundary.speech_id)
                elif boundary.speech_id in self._input_scopes:
                    self._input_scopes.remove(boundary.speech_id)
                # Inject at this position in the transport's media queue. A
                # timestamped LLM end travels through a separate clock queue
                # and can overtake audio; the untimed boundary cannot. Processing
                # inline here also prevents a later response overtaking it in
                # the processor's input queue. The original frame is unchanged.
                if boundary is not frame:
                    boundary.transport_destination = frame.transport_destination
                    await processor.process_frame(boundary, FrameDirection.DOWNSTREAM)
            elif (
                isinstance(frame, (TTSAudioRawFrame, SpeechOutputAudioRawFrame))
                and self._input_scopes
            ):
                if speech := self.pending.get(self._input_scopes[-1]):
                    speech.queued_seconds += len(frame.audio) / (
                        frame.sample_rate * frame.num_channels * 2
                    )
            elif isinstance(frame, TextFrame) and self._input_scopes:
                if self.greeting and self._input_scopes[-1] == self.greeting.id:
                    frame.metadata["dograh_greeting_id"] = self.greeting.id

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
                    if speech is self.greeting and self.on_greeting_started:
                        self.on_greeting_started(speech)
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
            if isinstance(frame, (TTSAudioRawFrame, SpeechOutputAudioRawFrame)):
                self.note_output(frame)
            elif self.greeting is None or self.greeting.done:
                self.note_output()
        elif isinstance(frame, EndFrame):
            self.cancel_all()

    def note_output(self, frame: OutputAudioRawFrame | None = None) -> None:
        """Record delivered audio, or delivered text in the text-chat adapter."""
        speech = (
            self.pending.get(self._output_scopes[-1]) if self._output_scopes else None
        )
        if speech:
            speech.has_output = True
            if speech.first_output_at is None:
                speech.first_output_at = datetime.now(UTC).isoformat(
                    timespec="milliseconds"
                )
                if speech is self.greeting:
                    now = asyncio.get_running_loop().time()
                    logger.bind(**speech.log_context).info(
                        "Greeting first output: speech_id={} first_output_at={} "
                        "request_to_output_ms={} connection_to_output_ms={} output_type={}",
                        speech.id,
                        speech.first_output_at,
                        round((now - speech.requested_at) * 1000),
                        round((now - speech.connection_started_at) * 1000)
                        if speech.connection_started_at is not None
                        else None,
                        "audio" if frame is not None else "text",
                    )
            if frame is not None:
                speech.played_seconds += len(frame.audio) / (
                    frame.sample_rate * frame.num_channels * 2
                )
        if self._output_scopes:
            for observer in self._observers:
                observer.on_output(self._output_scopes[-1])

    @staticmethod
    def _complete(speech: SpeechPlayback) -> None:
        speech.finish(
            PlaybackOutcome.PLAYED if speech.has_output else PlaybackOutcome.FAILED
        )
