"""The assistant WAV follows sent speech while the full recording retains ambience."""

import asyncio
import io
import struct
import wave

import numpy as np
import pytest
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    InterruptionFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams
from pipecat.tests.mock_transport import MockOutputTransport
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transports.base_transport import TransportParams

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.event_handlers import register_audio_data_handler
from api.services.pipecat.in_memory_buffers import InMemoryRecordingBuffers
from api.services.pipecat.pipeline_builder import create_pipeline_components


def pcm(value, samples):
    return struct.pack("<h", value) * samples


def wav_pcm(data, sample_rate):
    with wave.open(io.BytesIO(data), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == sample_rate
        return wav.readframes(wav.getnframes())


def assert_pcm_matches(actual, expected):
    # SOXR dithers 16-bit output; independent resamplers can differ by two LSBs.
    np.testing.assert_allclose(
        np.frombuffer(actual, dtype="<i2").astype(np.int32),
        np.frombuffer(expected, dtype="<i2").astype(np.int32),
        atol=2,
        rtol=0,
    )


class CapturingOutputTransport(MockOutputTransport):
    def __init__(self, params):
        super().__init__(params)
        self.accepted = []
        self.rejected = 0

    async def write_audio_frame(self, frame):
        await super().write_audio_frame(frame)
        clean = frame.unmixed_audio if frame.unmixed_audio is not None else frame.audio
        if np.any(np.frombuffer(clean, dtype="<i2") == 4000):
            self.rejected += 1
            return False
        self.accepted.append((frame.audio, clean))
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_rate", [8000, 16000, 24000])
@pytest.mark.parametrize(
    "scenario", ["normal", "interruption", "write_failure", "no_ambient"]
)
async def test_clean_bot_wav_matches_sent_audio(tmp_path, transport_rate, scenario):
    recording_rate = min(transport_rate, 16000)
    mixer = None
    if scenario != "no_ambient":
        ambient_path = tmp_path / "ambient.wav"
        with wave.open(str(ambient_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(transport_rate)
            wav.writeframes(pcm(6000, transport_rate * 2))
        mixer = SoundfileMixer(
            sound_files={"ambient": str(ambient_path)},
            default_sound="ambient",
            volume=0.5,
        )

    transport = CapturingOutputTransport(
        TransportParams(
            audio_out_enabled=True,
            audio_out_sample_rate=transport_rate,
            audio_out_mixer=mixer,
            audio_out_end_silence_secs=0,
            audio_out_max_consecutive_failures=100,
            audio_out_sleep_between_failures=0,
        )
    )
    recording, _ = create_pipeline_components(
        AudioConfig(
            transport_in_sample_rate=transport_rate,
            transport_out_sample_rate=transport_rate,
            pipeline_sample_rate=recording_rate,
            buffer_size_seconds=0.04,
        )
    )
    buffers = InMemoryRecordingBuffers(1, recording_rate)
    register_audio_data_handler(recording, 1, buffers)
    await recording.start_recording()

    speech_samples = transport_rate // (2 if scenario == "interruption" else 10)
    frames = [
        SleepFrame(0.06),
        TTSAudioRawFrame(pcm(2000, speech_samples), transport_rate, 1),
    ]
    if scenario == "interruption":
        frames.extend([SleepFrame(0.06), InterruptionFrame(), SleepFrame(0.06)])
    if scenario == "write_failure":
        frames.append(
            TTSAudioRawFrame(pcm(4000, transport_rate // 10), transport_rate, 1)
        )
    frames.extend(
        [
            TTSStoppedFrame(),
            SleepFrame(0.2),
            # Recorded greetings also use OutputAudioRawFrame, without TTS metadata.
            # Adding ambience clips these samples, so subtraction cannot recover them.
            OutputAudioRawFrame(pcm(30000, transport_rate // 10), transport_rate, 1),
            TTSStoppedFrame(),
        ]
    )
    async with asyncio.timeout(5):
        await run_test(
            Pipeline([transport, recording]),
            frames_to_send=frames,
            pipeline_params=PipelineParams(
                audio_in_sample_rate=recording_rate,
                audio_out_sample_rate=transport_rate,
            ),
        )

    bot = wav_pcm(await buffers.bot.to_wav_bytes(), recording_rate)
    mixed = wav_pcm(await buffers.mixed.to_wav_bytes(), recording_rate)
    user = wav_pcm(await buffers.user.to_wav_bytes(), recording_rate)
    assert len(bot) == len(mixed) == len(user) > 0
    assert not any(user)

    sent_mixed = b"".join(pair[0] for pair in transport.accepted)
    sent_clean = b"".join(pair[1] for pair in transport.accepted)
    # Independent reference streams verify both recording resamplers and chunk flushes.
    expected_clean = await create_stream_resampler().resample(
        sent_clean, transport_rate, recording_rate
    )
    expected_mixed = await create_stream_resampler().resample(
        sent_mixed, transport_rate, recording_rate
    )
    assert_pcm_matches(bot, expected_clean)
    assert_pcm_matches(mixed, expected_mixed)
    assert pcm(30000, transport_rate // 10) in sent_clean
    if mixer:
        assert mixed != bot
        assert pcm(0, transport_rate // 100) in sent_clean
        assert pcm(32767, transport_rate // 10) in sent_mixed
    else:
        assert_pcm_matches(mixed, bot)
    if scenario == "interruption":
        assert 0 < sent_clean.count(pcm(2000, 1)) < speech_samples
    if scenario == "write_failure":
        assert transport.rejected > 0
        assert pcm(4000, 1) not in sent_clean
