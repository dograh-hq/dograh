from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import YandexRealtimeLLMConfiguration
from api.services.pipecat.realtime.yandex_realtime import DograhYandexRealtimeLLMService
from api.services.pipecat.service_factory import create_realtime_llm_service
from pipecat.services.yandex.realtime.llm import YANDEX_REALTIME_BASE_URL


def _make_service(**overrides) -> DograhYandexRealtimeLLMService:
    kwargs = {
        "api_key": "test-key",
        "settings": DograhYandexRealtimeLLMService.Settings(
            model="gpt://folder-1/speech-realtime-deepseek-v4-flash/latest"
        ),
    }
    kwargs.update(overrides)
    return DograhYandexRealtimeLLMService(**kwargs)


def test_base_url_defaults_to_yandex_endpoint():
    service = _make_service(
        settings=DograhYandexRealtimeLLMService.Settings(
            model="gpt://folder-1/speech-realtime-deepseek-v4-flash/latest"
        ),
    )

    assert service.base_url == (
        f"{YANDEX_REALTIME_BASE_URL}"
        "?model=gpt://folder-1/speech-realtime-deepseek-v4-flash/latest"
    )


@pytest.mark.asyncio
async def test_connect_authenticates_with_api_key_header(monkeypatch):
    service = _make_service()
    connect = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(
        "pipecat.services.yandex.realtime.llm.websocket_connect", connect
    )
    service._receive_task_handler = MagicMock(return_value=object())
    service.create_task = MagicMock()

    await service._connect()

    connect.assert_awaited_once_with(
        uri=service.base_url,
        additional_headers={"Authorization": "Api-Key test-key"},
    )


def test_factory_creates_dograh_yandex_realtime_service():
    effective_config = EffectiveAIModelConfiguration(
        is_realtime=True,
        realtime=YandexRealtimeLLMConfiguration(
            provider="yandex_realtime",
            api_key="yandex-key",
            folder_id="b1gxxxxxxxxxxxxxxxxx",
            model="speech-realtime-deepseek-v4-flash/latest",
            voice="masha",
        ),
    )

    service = create_realtime_llm_service(
        effective_config,
        audio_config=SimpleNamespace(),
    )

    assert isinstance(service, DograhYandexRealtimeLLMService)
    assert service._settings.model == (
        "gpt://b1gxxxxxxxxxxxxxxxxx/speech-realtime-deepseek-v4-flash/latest"
    )
    assert service.base_url == (
        "wss://ai.api.cloud.yandex.net/v1/realtime/openai"
        "?model=gpt://b1gxxxxxxxxxxxxxxxxx/speech-realtime-deepseek-v4-flash/latest"
    )
    assert service._settings.session_properties.audio.output.voice == "masha"
