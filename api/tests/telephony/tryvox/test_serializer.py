import base64
import json

import pytest

from pipecat.frames.frames import AudioRawFrame, InputAudioRawFrame, InterruptionFrame

from api.services.telephony.providers.tryvox.serializers import TryVoxFrameSerializer


def _serializer() -> TryVoxFrameSerializer:
    serializer = TryVoxFrameSerializer(
        call_id="call-123",
        auth_id="TJaccount",
        auth_token="account-token",
        api_base_url="https://api.tryvox.test",
        params=TryVoxFrameSerializer.InputParams(
            tryvox_sample_rate=8000,
            sample_rate=8000,
            auto_hang_up=False,
        ),
    )
    serializer._sample_rate = 8000
    return serializer


@pytest.mark.asyncio
async def test_serializes_pcm_as_tryvox_play_audio():
    serializer = _serializer()
    pcm = b"\x01\x00\xff\x7f" * 80

    encoded = await serializer.serialize(
        AudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1)
    )
    message = json.loads(encoded)

    assert message["type"] == "playAudio"
    assert message["data"]["audioContentType"] == "raw"
    assert message["data"]["sampleRate"] == 8000
    assert base64.b64decode(message["data"]["audioContent"]) == pcm


@pytest.mark.asyncio
async def test_deserializes_binary_pcm_without_json_wrapper():
    serializer = _serializer()
    pcm = b"\x01\x00\x02\x00" * 80

    frame = await serializer.deserialize(pcm)

    assert isinstance(frame, InputAudioRawFrame)
    assert frame.audio == pcm
    assert frame.sample_rate == 8000
    assert frame.num_channels == 1


@pytest.mark.asyncio
async def test_metadata_and_interruption_do_not_emit_provider_commands():
    serializer = _serializer()

    assert await serializer.deserialize('{"workflow_run_id":"13"}') is None
    assert await serializer.serialize(InterruptionFrame()) is None
