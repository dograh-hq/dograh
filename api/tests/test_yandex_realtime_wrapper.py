from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.pipecat.realtime.yandex_realtime import (
    YANDEX_REALTIME_BASE_URL,
    DograhYandexRealtimeLLMService,
)


def _make_service(**overrides) -> DograhYandexRealtimeLLMService:
    kwargs = {"api_key": "test-key"}
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
        "api.services.pipecat.realtime.yandex_realtime.websocket_connect", connect
    )
    service._receive_task_handler = MagicMock(return_value=object())
    service.create_task = MagicMock()

    await service._connect()

    connect.assert_awaited_once_with(
        uri=service.base_url,
        additional_headers={"Authorization": "Api-Key test-key"},
    )
