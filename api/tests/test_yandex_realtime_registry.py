import pytest
from pydantic import ValidationError

from api.services.configuration.options import YANDEX_REALTIME_API_VERSIONS
from api.services.configuration.registry import YandexRealtimeLLMConfiguration


def test_yandex_realtime_api_versions_only_advertise_v1():
    assert YANDEX_REALTIME_API_VERSIONS == ["v1"]


def test_yandex_realtime_config_requires_folder_id():
    with pytest.raises(ValidationError, match="folder_id"):
        YandexRealtimeLLMConfiguration(
            provider="yandex_realtime",
            api_key="test-key",
        )


def test_yandex_realtime_config_builds_with_folder_id():
    config = YandexRealtimeLLMConfiguration(
        provider="yandex_realtime",
        api_key="test-key",
        folder_id="b1gxxxxxxxxxxxxxxxxx",
    )

    assert config.folder_id == "b1gxxxxxxxxxxxxxxxxx"
    assert config.model == "speech-realtime-deepseek-v4-flash/latest"
    assert config.voice == "masha"
    assert config.api_version == "v1"
