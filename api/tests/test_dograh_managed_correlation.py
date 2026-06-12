import json

import pytest
from openai._types import NOT_GIVEN as OPENAI_NOT_GIVEN
from pipecat.frames.frames import TTSStartedFrame
from pipecat.services.dograh.llm import DograhLLMService
from pipecat.services.dograh.stt import DograhSTTService
from pipecat.services.dograh.tts import DograhTTSService
from pipecat.services.openai.base_llm import OpenAILLMSettings
from websockets.protocol import State


class _FakeWebSocket:
    def __init__(self):
        self.state = State.OPEN
        self.messages: list[dict] = []

    async def send(self, message: str) -> None:
        self.messages.append(json.loads(message))

    async def close(self, *args, **kwargs) -> None:
        self.state = State.CLOSED


def test_dograh_llm_uses_explicit_mps_correlation_id():
    service = DograhLLMService(
        api_key="mps-secret",
        correlation_id="mps-corr-123",
        settings=OpenAILLMSettings(model="default"),
    )
    service._start_metadata = {"workflow_run_id": 99}

    params = service.build_chat_completion_params(
        {
            "messages": [],
            "tools": OPENAI_NOT_GIVEN,
            "tool_choice": OPENAI_NOT_GIVEN,
        }
    )

    assert params["metadata"]["correlation_id"] == "mps-corr-123"
    assert params["metadata"]["mps_billing_version"] == "2"


@pytest.mark.asyncio
async def test_dograh_stt_config_uses_explicit_mps_correlation_id(monkeypatch):
    fake_ws = _FakeWebSocket()

    async def fake_connect(url, additional_headers):
        return fake_ws

    monkeypatch.setattr(
        "pipecat.services.dograh.stt.websocket_connect",
        fake_connect,
    )

    service = DograhSTTService(
        api_key="mps-secret",
        correlation_id="mps-corr-123",
        sample_rate=16000,
    )
    service._start_metadata = {"workflow_run_id": 99}

    await service._connect_websocket()

    assert fake_ws.messages[0]["type"] == "config"
    assert fake_ws.messages[0]["correlation_id"] == "mps-corr-123"
    assert fake_ws.messages[0]["mps_billing_version"] == "2"


@pytest.mark.asyncio
async def test_dograh_tts_messages_use_explicit_mps_correlation_id(monkeypatch):
    fake_ws = _FakeWebSocket()

    async def fake_connect(url, additional_headers):
        return fake_ws

    monkeypatch.setattr(
        "pipecat.services.dograh.tts.websocket_connect",
        fake_connect,
    )

    service = DograhTTSService(
        api_key="mps-secret",
        correlation_id="mps-corr-123",
        sample_rate=24000,
    )
    service._start_metadata = {"workflow_run_id": 99}

    await service._connect_websocket()
    assert fake_ws.messages[0]["type"] == "config"
    assert fake_ws.messages[0]["correlation_id"] == "mps-corr-123"
    assert fake_ws.messages[0]["mps_billing_version"] == "2"

    async def _noop(*args, **kwargs):
        return None

    service.audio_context_available = lambda context_id: False
    service.create_audio_context = _noop
    service.start_ttfb_metrics = _noop
    service.start_tts_usage_metrics = _noop

    frames = []
    async for frame in service.run_tts("hello", "ctx-1"):
        frames.append(frame)

    assert isinstance(frames[0], TTSStartedFrame)
    assert fake_ws.messages[1]["type"] == "create_context"
    assert fake_ws.messages[1]["correlation_id"] == "mps-corr-123"
    assert fake_ws.messages[1]["mps_billing_version"] == "2"
