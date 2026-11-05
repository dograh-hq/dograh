from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import OpenRouterLLMConfiguration
from api.services.pipecat.service_factory import create_llm_service


def _create_openrouter(**llm):
    config = OpenRouterLLMConfiguration(api_key="test-key", **llm)
    with patch(
        "api.services.pipecat.service_factory.OpenRouterLLMService"
    ) as mock_service:
        create_llm_service(SimpleNamespace(llm=config))
    return mock_service.call_args.kwargs


def test_openrouter_llm_sends_provider_order_in_request_body():
    kwargs = _create_openrouter(provider_order=["provider-a", "provider-b"])

    assert kwargs["settings"].extra == {
        "extra_body": {"provider": {"order": ["provider-a", "provider-b"]}}
    }


def test_openrouter_llm_without_provider_order_keeps_default_routing():
    kwargs = _create_openrouter()

    assert kwargs["settings"].extra == {}
    assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
