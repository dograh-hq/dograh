"""WAV synthesis through normal TTS frame processing and asynchronous receivers."""

import asyncio
import io
import wave

import pytest
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.tests.mock_tts_service import MockTTSService

from api.services.pipecat.speech_synthesis import synthesize_speech

PCM = b"\x01\x00" * 1280


class TrackingTTS(MockTTSService):
    def __init__(self, **kwargs):
        super().__init__(mock_audio_data=PCM, frame_delay=0, **kwargs)
        self.cleaned = False

    async def cleanup(self):
        await super().cleanup()
        self.cleaned = True


class ReceiverTTS(TrackingTTS):
    """Like a WebSocket adapter, deliver audio after run_tts returns."""

    async def run_tts(self, text, context_id):
        self.create_task(self.receive(context_id))
        yield None

    async def receive(self, context_id):
        for chunk in (PCM[:501], PCM[501:]):
            await asyncio.sleep(0.02)
            await self.append_to_audio_context(
                context_id,
                TTSAudioRawFrame(chunk, 16000, 1, context_id=context_id),
            )
        await self.append_to_audio_context(
            context_id, TTSStoppedFrame(context_id=context_id)
        )
        await self.remove_audio_context(context_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("service_type", [TrackingTTS, ReceiverTTS])
@pytest.mark.parametrize("push_text_frames", [True, False])
@pytest.mark.parametrize("pause_frame_processing", [True, False])
async def test_collects_complete_wav_and_cleans_processor(
    service_type, push_text_frames, pause_frame_processing
):
    tts = service_type(
        push_text_frames=push_text_frames,
        pause_frame_processing=pause_frame_processing,
    )
    audio = await synthesize_speech(tts, "Hello.", timeout=2)
    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.readframes(wav.getnframes()) == PCM
    assert tts.cleaned


@pytest.mark.asyncio
async def test_rejects_long_audio_and_cleans_processor():
    tts = TrackingTTS()
    with pytest.raises(ValueError):
        await synthesize_speech(tts, "Hello.", max_duration=0.01, timeout=2)
    assert tts.cleaned


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("external_cancel", [False, True])
async def test_deadline_and_cancellation_stop_pending_synthesis(
    background, external_cancel
):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowTTS(TrackingTTS):
        receiver = None

        async def wait_forever(self):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def run_tts(self, text, context_id):
            if background:
                self.receiver = self.create_task(self.wait_forever())
            else:
                await self.wait_forever()
            yield None

        async def cleanup(self):
            if self.receiver is not None:
                await self.cancel_task(self.receiver)
            await super().cleanup()

    tts = SlowTTS()
    if external_cancel:
        task = asyncio.create_task(synthesize_speech(tts, "Hello.", timeout=2))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    else:
        with pytest.raises(TimeoutError):
            await synthesize_speech(tts, "Hello.", timeout=2)
    assert cancelled.is_set()
    assert tts.cleaned


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_discards_partial_audio_on_provider_error(background):
    class FailedTTS(TrackingTTS):
        async def run_tts(self, text, context_id):
            async for frame in super().run_tts(text, context_id):
                yield frame
            if background:
                await self.push_error_frame(ErrorFrame("test synthesis failure"))
            else:
                yield ErrorFrame("test synthesis failure")

    tts = FailedTTS()
    with pytest.raises(ValueError):
        await synthesize_speech(tts, "Hello.", timeout=2)
    assert tts.cleaned


@pytest.mark.asyncio
@pytest.mark.parametrize("pcm,rate", [(b"x", 16000), (PCM, 0)])
async def test_rejects_invalid_pcm(pcm, rate):
    class InvalidTTS(TrackingTTS):
        async def run_tts(self, text, context_id):
            yield TTSAudioRawFrame(pcm, rate, 1, context_id=context_id)

    tts = InvalidTTS()
    with pytest.raises(ValueError):
        await synthesize_speech(tts, "Hello.", timeout=2)
    assert tts.cleaned


@pytest.mark.asyncio
async def test_empty_synthesis_is_not_a_valid_wav():
    tts = TrackingTTS()
    with pytest.raises(ValueError):
        await synthesize_speech(tts, " ", timeout=2)
    assert tts.cleaned


@pytest.mark.asyncio
async def test_cannot_rewire_a_processor_from_another_pipeline():
    tts = TrackingTTS()
    output = FrameProcessor()
    tts.link(output)
    with pytest.raises(ValueError, match="unlinked"):
        await synthesize_speech(tts, "Hello.")
    assert tts.next is output
    assert not tts.cleaned


@pytest.mark.asyncio
async def test_cancellation_during_connection_start_closes_processor():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class ConnectingTTS(TrackingTTS):
        async def start(self, frame):
            await super().start(frame)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    tts = ConnectingTTS()
    task = asyncio.create_task(synthesize_speech(tts, "Hello.", timeout=2))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert cancelled.is_set()
    assert tts.cleaned
