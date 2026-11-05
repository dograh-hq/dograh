"""Retaining opening input must not delay, duplicate, or erase call recordings."""

import asyncio

import pytest
from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import SleepFrame, run_test

from api.services.pipecat.call_audio_buffer import CallAudioBufferProcessor
from api.services.pipecat.call_opening import OpeningInputGate


@pytest.mark.asyncio
@pytest.mark.parametrize("release", [False, True])
async def test_opening_input_is_recorded_at_arrival_once_even_if_call_ends_while_held(
    release,
):
    recording = CallAudioBufferProcessor(sample_rate=16000)
    captured = []
    stopped = asyncio.Event()

    @recording.event_handler("on_recording_stopped")
    async def on_stopped(_processor):
        stopped.set()

    @recording.event_handler("on_track_audio_data")
    async def on_audio(_processor, user, bot, rate, channels):
        captured.append(user)

    class Release(Frame):
        pass

    class Gate(OpeningInputGate):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, Release):
                # Recording happens while the caller's audio is still held.
                async with asyncio.timeout(1):
                    while not recording.has_audio():
                        await asyncio.sleep(0)
                if release:
                    await self.release()

    gate = Gate(capture_audio=recording.capture_opening_audio)
    audio = b"\x01\x02" * 1600
    frame = InputAudioRawFrame(audio, 16000, 1)
    await recording.start_recording()
    downstream, _ = await run_test(
        Pipeline([gate, recording]),
        frames_to_send=[frame, SleepFrame(0.02), Release(), SleepFrame(0.02)],
    )
    # run_test sends EndFrame, which must stop and flush recording itself.
    # An explicit stop here would hide a broken pipeline shutdown path.
    assert stopped.is_set()
    user = b"".join(captured)
    assert user.count(b"\x01\x02") == 1600
    assert sum(isinstance(frame, InputAudioRawFrame) for frame in downstream) == int(
        release
    )
    assert frame.audio == audio
