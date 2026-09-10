from unittest.mock import AsyncMock
import pytest
from pipecat.frames.frames import AudioRawFrame, CancelFrame, EndFrame, StartFrame

from api.services.telephony.providers.papi_voip import _config_loader
from api.services.telephony.providers.papi_voip.serializers import (
    PAPI_FRAME_BYTES,
    PAPI_SAMPLE_RATE,
    PapiVoipFrameSerializer,
)


def test_config_loader_does_not_contain_from_numbers():
    raw_credentials = {
        "api_key": "test-api-key",
        "instance_id": "inst-123",
        "base_url": "https://api.papi.api.br",
        "from_numbers": ["+5511999999999"],
    }
    loaded = _config_loader(raw_credentials)
    assert "from_numbers" not in loaded
    assert loaded["api_key"] == "test-api-key"
    assert loaded["instance_id"] == "inst-123"
    assert loaded["base_url"] == "https://api.papi.api.br"
    assert loaded["provider"] == "papi_voip"


@pytest.mark.asyncio
async def test_serializer_preserves_all_frames_on_audio_raw_frame():
    serializer = PapiVoipFrameSerializer(
        call_id="call-123",
        base_url="https://api.papi.api.br",
        api_key="key",
        instance_id="inst-1",
    )
    await serializer.setup(StartFrame(audio_in_sample_rate=16000))

    # Send 2 full frames worth of 16kHz 16-bit mono audio (2 * 1920 = 3840 bytes)
    sample_pcm = b"\x01\x00" * 1920
    assert len(sample_pcm) == 3840

    frame = AudioRawFrame(audio=sample_pcm, sample_rate=16000, num_channels=1)
    result = await serializer.serialize(frame)

    assert result is not None
    assert len(result) == 3840  # Both frames returned combined (no dropouts)


@pytest.mark.asyncio
async def test_serializer_flushes_all_remaining_frames_on_end_frame():
    hangup_mock = AsyncMock()
    hangup_strategy = AsyncMock()
    hangup_strategy.execute_hangup = hangup_mock

    serializer = PapiVoipFrameSerializer(
        call_id="call-123",
        base_url="https://api.papi.api.br",
        api_key="key",
        instance_id="inst-1",
        hangup_strategy=hangup_strategy,
    )
    await serializer.setup(StartFrame(audio_in_sample_rate=16000))

    # Send 1.5 frames of audio (2880 bytes: 1920 + 960)
    audio_data = b"\x02\x00" * 1440
    frame = AudioRawFrame(audio=audio_data, sample_rate=16000, num_channels=1)
    first_res = await serializer.serialize(frame)
    assert len(first_res) == 1920  # 1 full frame returned initially

    # Send EndFrame -> should flush remaining 960 bytes padded to 1920 bytes and execute hangup
    end_frame = EndFrame()
    end_res = await serializer.serialize(end_frame)

    assert end_res is not None
    assert len(end_res) == 1920  # Remaining frame zero-padded to 1920
    assert end_res[:960] == audio_data[1920:]
    assert end_res[960:] == b"\x00" * 960
    hangup_mock.assert_awaited_once()
