"""Call-owned speech completion and muting, independent of bot activity events."""

import asyncio
import uuid
from enum import Enum

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputAudioRawFrame,
    SpeechBoundaryFrame,
    StopFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class PlaybackOutcome(Enum):
    PLAYED = "played"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    CLOSED = "closed"


class SpeechPlayback:
    """One speech request; its deadline and mute outlive any individual waiter."""

    def __init__(
        self, owner: "SpeechPlaybackTracker", *, mute_user: bool, timeout: float
    ):
        self.id = str(uuid.uuid4())
        self.mute_user = mute_user
        self.has_output = False
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


class SpeechPlaybackTracker:
    """Resolve speech at output boundaries and release every request's own mute."""

    def __init__(self):
        self.pending: dict[str, SpeechPlayback] = {}
        self._output: FrameProcessor | None = None
        # Direct recordings can nest inside a still-generating TTS response.
        self._output_scopes: list[str] = []
        self._response: str | None = None
        self._expected_response: SpeechPlayback | None = None
        self._expected_source: FrameProcessor | None = None

    @property
    def mutes_user(self) -> bool:
        return any(speech.mute_user for speech in self.pending.values())

    def create(self, *, mute_user: bool = False, timeout: float = 35) -> SpeechPlayback:
        speech = SpeechPlayback(self, mute_user=mute_user, timeout=timeout)
        self.pending[speech.id] = speech
        return speech

    def expect_response(self, *, source=None, **kwargs) -> SpeechPlayback:
        """Own the next response emitted by the LLM, before requesting it.

        Tag its start at the source so an earlier response still buffered in TTS
        cannot claim this operation when it eventually reaches output.
        """
        if self._expected_response and not self._expected_response.done:
            raise RuntimeError("A generated response is already pending")
        speech = self.create(**kwargs)
        self._expected_response = speech
        self._expected_source = source if isinstance(source, FrameProcessor) else None
        if self._expected_source:

            def mark_response(_processor, frame):
                if (
                    isinstance(frame, LLMFullResponseStartFrame)
                    and self._expected_response is speech
                    and not speech.done
                ):
                    frame.metadata["dograh_speech_id"] = speech.id
                    self._expected_response = None
                    self._expected_source = None

            source.add_event_handler("on_before_push_frame", mark_response)
            speech._result.add_done_callback(
                lambda _: source.remove_event_handler(
                    "on_before_push_frame", mark_response
                )
            )
        return speech

    def cancel_all(self, outcome: PlaybackOutcome = PlaybackOutcome.CLOSED) -> None:
        for speech in list(self.pending.values()):
            speech.finish(outcome)
        self._output_scopes.clear()
        self._response = None
        self._expected_response = None
        self._expected_source = None

    def bind_output(self, output) -> None:
        if self._output:
            self._output.remove_event_handler(
                "on_before_process_frame", self.before_output
            )
            self._output.remove_event_handler("on_after_push_frame", self.after_output)
        self._output = output if isinstance(output, FrameProcessor) else None
        if self._output:
            self._output.add_event_handler(
                "on_before_process_frame", self.before_output
            )
            self._output.add_event_handler("on_after_push_frame", self.after_output)

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
        if isinstance(frame, LLMFullResponseStartFrame):
            self._response = frame.metadata.get("dograh_speech_id")
            if self._expected_source is None and self._expected_response:
                # Adapters without a FrameProcessor source observe generation
                # directly (also useful for transport-only tests).
                self._response = self._expected_response.id
                self._expected_response = None
            if self._response:
                return SpeechBoundaryFrame(self._response, beginning=True)
        elif isinstance(frame, LLMFullResponseEndFrame) and self._response:
            speech_id = self._response
            self._response = None
            return SpeechBoundaryFrame(speech_id, beginning=False)
        return None

    def after_output(self, _processor, frame: Frame) -> None:
        """Called only after the transport has written the preceding audio."""
        if isinstance(frame, SpeechBoundaryFrame):
            if frame.beginning:
                self._output_scopes.append(frame.speech_id)
            else:
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

    @staticmethod
    def _complete(speech: SpeechPlayback) -> None:
        speech.finish(
            PlaybackOutcome.PLAYED if speech.has_output else PlaybackOutcome.FAILED
        )
