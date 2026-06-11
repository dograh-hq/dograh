import base64
import json
import math
import struct
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputDTMFFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
)

from api.services.telephony.providers.voicelink.serializers import (
    VoiceLinkFrameSerializer,
)

STREAM_SID = "MZ-voicelink-stream-1"
CALL_SID = "5b2f9c1e-aaaa-bbbb-cccc-1234567890ab"


def _pcm_sine(num_samples: int = 160, amplitude: int = 12000) -> bytes:
    samples = [
        int(amplitude * math.sin(2 * math.pi * 440 * i / 8000))
        for i in range(num_samples)
    ]
    return struct.pack(f"<{num_samples}h", *samples)


async def _serializer(sample_rate: int = 8000) -> VoiceLinkFrameSerializer:
    serializer = VoiceLinkFrameSerializer(
        stream_sid=STREAM_SID,
        call_sid=CALL_SID,
        params=VoiceLinkFrameSerializer.InputParams(
            voicelink_sample_rate=8000, sample_rate=sample_rate
        ),
    )
    await serializer.setup(SimpleNamespace(audio_in_sample_rate=sample_rate))
    return serializer


@pytest.mark.asyncio
async def test_serialize_audio_produces_alaw_media_event():
    serializer = await _serializer()
    pcm = _pcm_sine()

    message = await serializer.serialize(
        OutputAudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1)
    )

    event = json.loads(message)
    assert event["event"] == "media"
    assert event["stream_sid"] == STREAM_SID

    alaw = base64.b64decode(event["media"]["payload"])
    # A-law is one byte per 16-bit sample at the same rate
    assert len(alaw) == len(pcm) // 2


@pytest.mark.asyncio
async def test_alaw_round_trip_preserves_audio():
    serializer = await _serializer()
    pcm = _pcm_sine()

    message = await serializer.serialize(
        OutputAudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1)
    )
    event = json.loads(message)

    frame = await serializer.deserialize(
        json.dumps(
            {
                "event": "media",
                "media": {"track": "inbound", "payload": event["media"]["payload"]},
            }
        )
    )

    assert isinstance(frame, InputAudioRawFrame)
    assert frame.sample_rate == 8000
    assert frame.num_channels == 1
    assert len(frame.audio) == len(pcm)

    original = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    decoded = struct.unpack(f"<{len(frame.audio) // 2}h", frame.audio)
    # G.711 A-law quantization error is bounded; ensure the signal survives.
    max_error = max(abs(a - b) for a, b in zip(original, decoded))
    assert max_error < 1024


@pytest.mark.asyncio
async def test_interruption_serializes_to_clear_event():
    serializer = await _serializer()

    message = await serializer.serialize(InterruptionFrame())

    assert json.loads(message) == {"event": "clear", "stream_sid": STREAM_SID}


@pytest.mark.asyncio
async def test_deserialize_dtmf_event():
    serializer = await _serializer()

    frame = await serializer.deserialize(
        json.dumps({"event": "dtmf", "dtmf": {"digit": "5"}})
    )

    assert isinstance(frame, InputDTMFFrame)
    assert frame.button.value == "5"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {"event": "connected"},
        {"event": "mark", "mark": {"name": "m1"}},
        {"event": "stop", "stop": {"callSid": CALL_SID}},
        {"event": "transfer", "target": "9876543210"},
    ],
)
async def test_deserialize_ignores_non_media_events(event):
    serializer = await _serializer()

    assert await serializer.deserialize(json.dumps(event)) is None


@pytest.mark.asyncio
async def test_deserialize_empty_media_payload_returns_none():
    serializer = await _serializer()

    frame = await serializer.deserialize(
        json.dumps({"event": "media", "media": {"payload": ""}})
    )

    assert frame is None
