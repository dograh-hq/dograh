from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import TypeAdapter

from api.services.configuration import check_validity
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    HOPPER_API_BASE_URL,
    REGISTRY,
    HopperLLMConfiguration,
    LLMConfig,
    ServiceProviders,
    ServiceType,
)
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_llm_service_from_provider,
)


def test_hopper_llm_configuration_defaults_and_registry():
    config = HopperLLMConfiguration(api_key="sk_hopper_test")

    assert config.provider == ServiceProviders.HOPPER
    assert config.model == "gemma-4-31b"
    assert "base_url" not in HopperLLMConfiguration.model_fields
    assert REGISTRY[ServiceType.LLM][ServiceProviders.HOPPER] is HopperLLMConfiguration


def test_hopper_llm_discriminator_parses_llm_config():
    config = TypeAdapter(LLMConfig).validate_python(
        {
            "provider": "hopper",
            "api_key": "sk_hopper_test",
            "model": "gemma-4-31b",
        }
    )

    assert isinstance(config, HopperLLMConfiguration)
    assert config.model == "gemma-4-31b"


def test_create_hopper_llm_service_uses_openai_service_at_hopper_endpoint():
    user_config = SimpleNamespace(llm=HopperLLMConfiguration(api_key="sk_hopper_test"))

    with patch("api.services.pipecat.service_factory.OpenAILLMService") as mock_service:
        create_llm_service(user_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "sk_hopper_test"
    assert kwargs["base_url"] == HOPPER_API_BASE_URL
    assert kwargs["settings"].model == "gemma-4-31b"
    assert kwargs["settings"].temperature == 0.1


def test_create_hopper_llm_service_from_provider_ignores_caller_base_url():
    with patch("api.services.pipecat.service_factory.OpenAILLMService") as mock_service:
        create_llm_service_from_provider(
            ServiceProviders.HOPPER.value,
            "gemma-4-31b",
            "sk_hopper_test",
            base_url="https://example.com/v1",
        )

    kwargs = mock_service.call_args.kwargs
    assert kwargs["base_url"] == HOPPER_API_BASE_URL
    assert kwargs["settings"].model == "gemma-4-31b"


def test_hopper_api_key_validation_lists_models_at_hopper_endpoint(monkeypatch):
    captured = {}

    class FakeModels:
        def list(self):
            return []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.models = FakeModels()

    monkeypatch.setattr(check_validity.openai, "OpenAI", FakeOpenAI)

    config = HopperLLMConfiguration(api_key="sk_hopper_test")
    is_valid = UserConfigurationValidator()._check_api_key(
        ServiceProviders.HOPPER.value, "sk_hopper_test", config
    )

    assert is_valid is True
    assert captured == {"api_key": "sk_hopper_test", "base_url": HOPPER_API_BASE_URL}


def test_hopper_api_key_validation_reports_hopper_error(monkeypatch):
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

    config = HopperLLMConfiguration(api_key="sk_hopper_bad")
    with pytest.raises(ValueError) as exc_info:
        UserConfigurationValidator()._check_api_key(
            ServiceProviders.HOPPER.value, "sk_hopper_bad", config
        )

    message = str(exc_info.value)
    assert "Invalid Hopper API key" in message
    assert "Invalid OpenAI API key" not in message
