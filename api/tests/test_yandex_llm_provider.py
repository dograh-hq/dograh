from unittest.mock import patch

import pytest
from pydantic import ValidationError

from api.services.configuration.registry import ServiceProviders, YandexLLMService
from api.services.pipecat.service_factory import create_llm_service_from_provider


class TestYandexLLMConfiguration:
    def test_requires_folder_id(self):
        with pytest.raises(ValidationError, match="folder_id"):
            YandexLLMService(api_key="test-key")

    def test_default_values(self):
        config = YandexLLMService(api_key="test-key", folder_id="b1gxxxxxxxxxxxxxxxxx")
        assert config.provider == ServiceProviders.YANDEX
        assert config.model == "deepseek-v4-flash/latest"
        assert config.folder_id == "b1gxxxxxxxxxxxxxxxxx"


class TestYandexLLMServiceFactory:
    def test_builds_gpt_uri_model_from_folder_id(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.YANDEX.value,
                model="deepseek-v4-flash",
                api_key="test-key",
                folder_id="b1gxxxxxxxxxxxxxxxxx",
            )

        assert mock_service.call_count == 1
        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["base_url"] == "https://ai.api.cloud.yandex.net/v1"
        assert kwargs["settings"].model == "gpt://b1gxxxxxxxxxxxxxxxxx/deepseek-v4-flash"

    def test_base_url_is_fixed_and_not_user_overridable(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.YANDEX.value,
                model="deepseek-v4-flash",
                api_key="test-key",
                folder_id="b1gxxxxxxxxxxxxxxxxx",
                base_url="https://custom.example/v1",
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["base_url"] == "https://ai.api.cloud.yandex.net/v1"
        assert kwargs["settings"].model == "gpt://b1gxxxxxxxxxxxxxxxxx/deepseek-v4-flash"
