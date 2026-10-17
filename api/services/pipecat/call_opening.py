"""Call-owned opening deadline, input retention, and preparation tasks."""

import asyncio
from collections import deque

from loguru import logger

from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    StartFrame,
    StopFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class OpeningDeadline:
    def __init__(self):
        self.armed_at: float | None = None
        self.closed = asyncio.Event()

    def arm(self) -> None:
        if self.armed_at is None:
            self.armed_at = asyncio.get_running_loop().time()

    async def wait(self, milliseconds: int) -> bool:
        self.arm()
        remaining = (
            self.armed_at + milliseconds / 1000 - asyncio.get_running_loop().time()
        )
        try:
            await asyncio.wait_for(self.closed.wait(), max(0, remaining))
        except TimeoutError:
            return not self.closed.is_set()
        return False


class OpeningInputGate(FrameProcessor):
    """Retain caller input before opening, without blocking lifecycle frames.

    Cascade places this after recognition; realtime places it before its user
    aggregator. Only opening input is retained. Overflow fails the call instead
    of silently losing speech. The gate is released once, in arrival order.
    """

    def __init__(self, *, vad=None, capture_audio=None, max_audio_seconds: float = 90):
        super().__init__()
        self.heard_speech = False
        self._vad = vad
        self._capture_audio = capture_audio
        self._max_audio_seconds = max_audio_seconds
        self._audio_seconds = 0.0
        self._frames = deque()
        self._holding = True
        self._closed = False
        self._lock = asyncio.Lock()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (CancelFrame, EndFrame, StopFrame)):
            self.close()
        if isinstance(frame, StartFrame) and self._vad:
            self._vad.set_sample_rate(frame.audio_in_sample_rate)
        retained = (
            isinstance(
                frame,
                (
                    InputAudioRawFrame,
                    TranscriptionFrame,
                    InterimTranscriptionFrame,
                    ProposedUserStartedSpeakingFrame,
                    ProposedUserStoppedSpeakingFrame,
                    VADUserStartedSpeakingFrame,
                    VADUserStoppedSpeakingFrame,
                ),
            )
            and direction is FrameDirection.DOWNSTREAM
        )
        if not retained:
            await self.push_frame(frame, direction)
            return
        async with self._lock:
            if self._closed:
                return
            if not self._holding:
                await self.push_frame(frame, direction)
                return
            if isinstance(frame, InputAudioRawFrame):
                if self._capture_audio:
                    await self._capture_audio(frame)
                    if self._closed:
                        return
                self._audio_seconds += len(frame.audio) / (
                    frame.sample_rate * frame.num_channels * 2
                )
                if self._vad and not self.heard_speech:
                    state = await self._vad.analyze_audio(frame.audio)
                    if self._closed:
                        return
                    self.heard_speech = state in (VADState.SPEAKING, VADState.STOPPING)
            elif isinstance(
                frame, (ProposedUserStartedSpeakingFrame, VADUserStartedSpeakingFrame)
            ) or (
                isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame))
                and frame.text.strip()
            ):
                self.heard_speech = True
            if (
                self._audio_seconds > self._max_audio_seconds
                or len(self._frames) >= 10000
            ):
                self.close()
                await self.push_error(
                    error_msg="Opening input retention limit exceeded",
                    force_treat_as_permanent=True,
                )
                return
            self._frames.append((frame, direction))

    async def release(self) -> None:
        async with self._lock:
            while self._frames and not self._closed:
                frame, direction = self._frames.popleft()
                await self.push_frame(frame, direction)
            self._holding = False
            self._audio_seconds = 0

    def close(self) -> None:
        self._closed = True
        self._frames.clear()
        self._audio_seconds = 0

    async def cleanup(self):
        self.close()
        if self._vad:
            await self._vad.cleanup()
        await super().cleanup()


class CallOpening:
    def __init__(self):
        self.deadline = OpeningDeadline()
        self.input_gate: OpeningInputGate | None = None
        self._preparation: set[asyncio.Task] = set()

    def prepare(self, coroutine) -> None:
        if self.deadline.closed.is_set():
            coroutine.close()
            return
        task = asyncio.create_task(coroutine)
        self._preparation.add(task)
        task.add_done_callback(self._prepared)

    def _prepared(self, task) -> None:
        self._preparation.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "Opening preparation failed: {}", type(task.exception()).__name__
            )

    async def close(self) -> None:
        self.deadline.closed.set()
        if self.input_gate:
            self.input_gate.close()
        tasks = tuple(
            task for task in self._preparation if task is not asyncio.current_task()
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
