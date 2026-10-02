"""Generate a WAV using any standalone Pipecat TTS processor."""

import asyncio
import io
import wave

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ControlFrame,
    EndWorkerFrame,
    Frame,
    OutputAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.tts_service import TTSService
from pipecat.workers.runner import WorkerRunner


class _SynthesisCompleteFrame(ControlFrame):
    """Follow the utterance through the TTS processor's audio serialization queue."""


class _WavCollector(FrameProcessor):
    def __init__(self, max_duration: float):
        super().__init__()
        self.audio = bytearray()
        self.sample_rate = 0
        self.channels = 0
        self.completed = False
        self.max_duration = max_duration

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, OutputAudioRawFrame):
                self.sample_rate = self.sample_rate or frame.sample_rate
                self.channels = self.channels or frame.num_channels
                if (
                    not 0 < self.sample_rate <= 48000
                    or self.channels not in (1, 2)
                    or frame.sample_rate != self.sample_rate
                    or frame.num_channels != self.channels
                    or len(self.audio) + len(frame.audio)
                    > self.max_duration * self.sample_rate * self.channels * 2
                ):
                    await self.push_error("Invalid or oversized synthesis audio")
                    return
                self.audio.extend(frame.audio)
            elif isinstance(frame, TTSStartedFrame):
                await self.push_frame(
                    BotStartedSpeakingFrame(), FrameDirection.UPSTREAM
                )
            elif isinstance(frame, TTSStoppedFrame):
                # Consumption is immediate: there is no device playing the audio.
                await self.push_frame(
                    BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM
                )
            elif isinstance(frame, _SynthesisCompleteFrame):
                self.completed = True
                await self.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)
        await self.push_frame(frame, direction)

    def wav(self) -> bytes:
        if (
            not self.completed
            or not self.audio
            or len(self.audio) % (self.channels * 2)
        ):
            raise ValueError("Incomplete synthesis audio")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(self.audio)
        return output.getvalue()


async def synthesize_speech(
    tts: TTSService,
    text: str,
    *,
    sample_rate: int = 24000,
    timeout: float = 30,
    max_duration: float = 60,
) -> bytes:
    """Return a PCM16 WAV, consuming and closing a fresh, unlinked TTS processor.

    HTTP and WebSocket processors use their normal pipeline lifecycle. Never
    pass a processor already attached to a live call. Caller-owned sessions
    remain the caller's responsibility. Save the returned bytes to a .wav file
    or serve them with Content-Type: audio/wav.
    """
    if tts.previous or tts.next:
        raise ValueError("Synthesis requires a fresh, unlinked TTS processor")
    collector = _WavCollector(max_duration)
    worker = PipelineWorker(
        Pipeline([tts, collector]),
        params=PipelineParams(audio_out_sample_rate=sample_rate),
        enable_rtvi=False,
        enable_turn_tracking=False,
        cancel_on_idle_timeout=False,
        cancel_timeout_secs=1,
        setup_timeout_secs=timeout,
        start_timeout_secs=timeout,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    failed = False

    async def on_error(_processor, _frame):
        nonlocal failed
        failed = True
        await runner.cancel()

    tts.add_event_handler("on_error", on_error)
    collector.add_event_handler("on_error", on_error)

    @worker.event_handler("on_pipeline_started")
    async def on_started(_worker, _frame):
        await worker.queue_frames(
            [TTSSpeakFrame(text, append_to_context=False), _SynthesisCompleteFrame()]
        )

    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    try:
        # Shield keeps runner cleanup separate from caller cancellation. The
        # runner consumes CancelledError internally, so cancelling it directly
        # could otherwise turn an expired deadline into successful partial audio.
        await asyncio.wait_for(asyncio.shield(run_task), timeout)
    finally:
        await runner.cancel()
        await run_task
        tts.remove_event_handler("on_error", on_error)
    if failed:
        raise ValueError("Speech synthesis failed")
    return collector.wav()
