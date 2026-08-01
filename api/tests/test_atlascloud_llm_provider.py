import pytest
from pydantic import TypeAdapter

from api.services.configuration import check_validity
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    AtlasCloudLLMService,
    LLMConfig,
    ServiceProviders,
)


def test_atlascloud_llm_configuration_defaults():
    config = AtlasCloudLLMService(api_key="atlas-key")

    assert config.provider == ServiceProviders.ATLASCLOUD
    assert config.model == "qwen/qwen3.5-flash"
    assert config.base_url == "https://api.atlascloud.ai/v1"


def test_atlascloud_llm_discriminator_parses_llm_config():
    config = TypeAdapter(LLMConfig).validate_python(
        {
            "provider": "atlascloud",
            "api_key": "atlas-key",
            "model": "deepseek-ai/deepseek-v4-pro",
            "base_url": "https://api.atlascloud.ai/v1",
        }
    )

    assert isinstance(config, AtlasCloudLLMService)
    assert config.model == "deepseek-ai/deepseek-v4-pro"
    assert config.base_url == "https://api.atlascloud.ai/v1"


def test_atlascloud_api_key_validation_uses_atlascloud_base_url(monkeypatch):
    captured = {}

    class FakeModels:
        def list(self):
            return []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.models = FakeModels()

    monkeypatch.setattr(
        "api.services.configuration.check_validity.openai.OpenAI", FakeOpenAI
    )

    config = AtlasCloudLLMService(api_key="atlas-key")
    is_valid = UserConfigurationValidator()._check_api_key(
        ServiceProviders.ATLASCLOUD.value,
        "atlas-key",
        config,
    )

    assert is_valid is True
    assert captured == {
        "api_key": "atlas-key",
        "base_url": "https://api.atlascloud.ai/v1",
    }


def test_atlascloud_api_key_validation_uses_atlascloud_error_message(monkeypatch):
    class FakeAuthenticationError(Exception):
        pass

    class FakeModels:
        def list(self):
            raise FakeAuthenticationError

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(check_validity.openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        check_validity.openai, "AuthenticationError", FakeAuthenticationError
    )

    config = AtlasCloudLLMService(api_key="atlas-key")
    with pytest.raises(ValueError) as exc_info:
        UserConfigurationValidator()._check_api_key(
            ServiceProviders.ATLASCLOUD.value,
            "atlas-key",
            config,
        )

    message = str(exc_info.value)
    assert "Invalid Atlas Cloud API key" in message
    assert "Invalid OpenAI API key" not in message
