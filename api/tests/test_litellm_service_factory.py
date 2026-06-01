from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

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
        assert config.base_url == "http://localhost:4000"
        assert config.api_key is None

    def test_custom_model(self):
        config = LiteLLMLLMConfiguration(model="claude-sonnet-4-20250514")
        assert config.model == "claude-sonnet-4-20250514"

    def test_custom_base_url(self):
        config = LiteLLMLLMConfiguration(base_url="https://my-proxy.example.com")
        assert config.base_url == "https://my-proxy.example.com"

    def test_with_api_key(self):
        config = LiteLLMLLMConfiguration(api_key="sk-litellm-master-key")
        assert config.api_key == "sk-litellm-master-key"

    def test_api_key_optional(self):
        config = LiteLLMLLMConfiguration()
        assert config.api_key is None

    def test_bedrock_model_format(self):
        config = LiteLLMLLMConfiguration(
            model="bedrock/anthropic.claude-sonnet-4-20250514-v1:0"
        )
        assert config.model == "bedrock/anthropic.claude-sonnet-4-20250514-v1:0"

    def test_vertex_model_format(self):
        config = LiteLLMLLMConfiguration(model="vertex_ai/gemini-2.5-flash")
        assert config.model == "vertex_ai/gemini-2.5-flash"


class TestLiteLLMServiceFactory:
    def test_create_litellm_service_default_base_url(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="gpt-4.1",
                api_key="sk-test",
            )

        assert mock_service.call_count == 1
        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["base_url"] == "http://localhost:4000/v1"
        assert kwargs["settings"].model == "gpt-4.1"
        assert kwargs["settings"].temperature == 0.1

    def test_create_litellm_service_custom_base_url(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="claude-sonnet-4-20250514",
                api_key="sk-test",
                base_url="https://my-proxy.example.com",
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["base_url"] == "https://my-proxy.example.com/v1"
        assert kwargs["settings"].model == "claude-sonnet-4-20250514"

    def test_create_litellm_service_base_url_already_has_v1(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="gpt-4.1",
                api_key="sk-test",
                base_url="https://my-proxy.example.com/v1",
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["base_url"] == "https://my-proxy.example.com/v1"

    def test_create_litellm_service_base_url_trailing_slash(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="gpt-4.1",
                api_key="sk-test",
                base_url="http://localhost:4000/",
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["base_url"] == "http://localhost:4000/v1"

    def test_create_litellm_service_base_url_trailing_slash_with_v1(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="gpt-4.1",
                api_key="sk-test",
                base_url="http://localhost:4000/v1/",
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["base_url"] == "http://localhost:4000/v1"

    def test_create_litellm_service_no_api_key_uses_placeholder(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.LITELLM.value,
                model="gpt-4.1",
                api_key=None,
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "no-key-required"

    def test_create_litellm_service_rejects_private_ip_in_saas_mode(self):
        with patch(
            "api.utils.url_security.DEPLOYMENT_MODE", "saas"
        ):
            with pytest.raises(HTTPException) as exc_info:
                create_llm_service_from_provider(
                    provider=ServiceProviders.LITELLM.value,
                    model="gpt-4.1",
                    api_key="sk-test",
                    base_url="http://169.254.169.254",
                )
            assert exc_info.value.status_code == 400


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
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service(user_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["base_url"] == "https://my-proxy.example.com/v1"
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["settings"].model == "gpt-4.1"

    def test_create_llm_service_no_api_key(self):
        user_config = SimpleNamespace(
            llm=SimpleNamespace(
                provider=ServiceProviders.LITELLM.value,
                model="claude-sonnet-4-20250514",
                api_key=None,
                base_url="http://localhost:4000",
            )
        )

        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service(user_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "no-key-required"


class TestLiteLLMValidation:
    def test_check_litellm_missing_base_url(self):
        from api.services.configuration.check_validity import (
            UserConfigurationValidator,
        )

        validator = UserConfigurationValidator()
        service_config = SimpleNamespace(base_url=None)

        with pytest.raises(ValueError, match="base_url is required"):
            validator._check_litellm_api_key("litellm", service_config)

    def test_check_litellm_empty_base_url(self):
        from api.services.configuration.check_validity import (
            UserConfigurationValidator,
        )

        validator = UserConfigurationValidator()
        service_config = SimpleNamespace(base_url="")

        with pytest.raises(ValueError, match="base_url is required"):
            validator._check_litellm_api_key("litellm", service_config)

    def test_check_litellm_valid_base_url(self):
        from api.services.configuration.check_validity import (
            UserConfigurationValidator,
        )

        validator = UserConfigurationValidator()
        service_config = SimpleNamespace(base_url="http://localhost:4000")

        assert validator._check_litellm_api_key("litellm", service_config) is True
