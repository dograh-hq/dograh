from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.configuration.registry import (
    LiteLLMLLMConfiguration,
    ServiceProviders,
)
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_llm_service_from_provider,
)


class TestLiteLLMLLMConfiguration:
    def test_default_values(self):
        config = LiteLLMLLMConfiguration()
        assert config.provider == ServiceProviders.LITELLM
        assert config.model == "gpt-4.1"
        assert config.base_url is None
        assert config.api_key is None

    def test_custom_model(self):
        config = LiteLLMLLMConfiguration(model="claude-sonnet-4-20250514")
        assert config.model == "claude-sonnet-4-20250514"

    def test_with_api_key(self):
        config = LiteLLMLLMConfiguration(api_key="sk-litellm-master-key")
        assert config.api_key == "sk-litellm-master-key"

    def test_with_base_url_for_proxy_mode(self):
        config = LiteLLMLLMConfiguration(base_url="http://localhost:4000")
        assert config.base_url == "http://localhost:4000"

    def test_api_key_and_base_url_both_optional(self):
        config = LiteLLMLLMConfiguration()
        assert config.api_key is None
        assert config.base_url is None

    def test_bedrock_model_format(self):
        config = LiteLLMLLMConfiguration(
            model="bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
        )
        assert config.model == "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"

    def test_vertex_model_format(self):
        config = LiteLLMLLMConfiguration(model="vertex_ai/gemini-2.5-flash")
        assert config.model == "vertex_ai/gemini-2.5-flash"


class TestLiteLLMServiceFactory:
    def test_create_litellm_service_uses_litellm_llm_service(self):
        with patch(
            "api.services.pipecat.litellm_llm.LiteLLMLLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="gpt-4.1",
                api_key="sk-test",
            )

        assert mock_service.call_count == 1
        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["settings"].model == "gpt-4.1"
        assert kwargs["settings"].temperature == 0.1

    def test_create_litellm_service_no_api_key(self):
        with patch(
            "api.services.pipecat.litellm_llm.LiteLLMLLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="claude-sonnet-4-20250514",
                api_key=None,
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] is None
        assert kwargs["settings"].model == "claude-sonnet-4-20250514"

    def test_create_litellm_service_with_base_url(self):
        with patch(
            "api.services.pipecat.litellm_llm.LiteLLMLLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="gpt-4.1",
                api_key="sk-proxy-key",
                base_url="http://localhost:4000",
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_base"] == "http://localhost:4000"


class TestLiteLLMCreateLLMService:
    def test_create_llm_service_extracts_base_url(self):
        user_config = SimpleNamespace(
            llm=SimpleNamespace(
                provider=ServiceProviders.LITELLM.value,
                model="gpt-4.1",
                api_key="sk-test",
                base_url="https://my-proxy.example.com",
            )
        )

        with patch(
            "api.services.pipecat.litellm_llm.LiteLLMLLMService"
        ) as mock_service:
            create_llm_service(user_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_base"] == "https://my-proxy.example.com"
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["settings"].model == "gpt-4.1"

    def test_create_llm_service_no_api_key_no_base_url(self):
        user_config = SimpleNamespace(
            llm=SimpleNamespace(
                provider=ServiceProviders.LITELLM.value,
                model="anthropic/claude-haiku-4-5-20251001",
                api_key=None,
                base_url=None,
            )
        )

        with patch(
            "api.services.pipecat.litellm_llm.LiteLLMLLMService"
        ) as mock_service:
            create_llm_service(user_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] is None
        assert kwargs["api_base"] is None


class TestLiteLLMConfigDiscriminatedUnion:
    def test_litellm_parses_through_llm_config_union(self):
        from api.services.configuration.registry import LLMConfig
        from pydantic import TypeAdapter

        adapter = TypeAdapter(LLMConfig)
        config = adapter.validate_python(
            {
                "provider": "litellm",
                "model": "claude-sonnet-4-20250514",
                "base_url": "https://my-proxy.example.com",
                "api_key": "sk-master",
            }
        )
        assert isinstance(config, LiteLLMLLMConfiguration)
        assert config.provider == ServiceProviders.LITELLM
        assert config.model == "claude-sonnet-4-20250514"

    def test_litellm_parses_through_user_configuration(self):
        from api.schemas.user_configuration import UserConfiguration

        uc = UserConfiguration(
            llm={
                "provider": "litellm",
                "model": "gpt-4.1",
            }
        )
        assert isinstance(uc.llm, LiteLLMLLMConfiguration)
        assert uc.llm.model == "gpt-4.1"
        assert uc.llm.api_key is None
        assert uc.llm.base_url is None

    def test_litellm_config_json_round_trip(self):
        config = LiteLLMLLMConfiguration(
            model="bedrock/anthropic.claude-sonnet-4-20250514-v1:0",
            base_url="https://proxy.corp.internal",
            api_key="sk-virtual-key",
        )
        data = config.model_dump()
        restored = LiteLLMLLMConfiguration(**data)
        assert restored.model == config.model
        assert restored.base_url == config.base_url
        assert restored.provider == ServiceProviders.LITELLM

    def test_litellm_registered_in_llm_registry(self):
        from api.services.configuration.registry import REGISTRY, ServiceType

        assert ServiceProviders.LITELLM in REGISTRY[ServiceType.LLM]


class TestLiteLLMValidation:
    def test_litellm_validation_passes_without_api_key_or_base_url(self):
        from api.services.configuration.check_validity import (
            UserConfigurationValidator,
        )

        validator = UserConfigurationValidator()
        result = validator._validate_service(
            LiteLLMLLMConfiguration(model="gpt-4.1"),
            "llm",
        )
        assert result == []
