from unittest.mock import patch

import pytest

from api.services.pipecat.service_factory import create_llm_service_from_provider


def _settings(model: str, provider: str = "openai"):
    with patch("api.services.pipecat.service_factory.OpenAILLMService") as mock_service:
        create_llm_service_from_provider(
            provider=provider, model=model, api_key="test-key"
        )
    return mock_service.call_args.kwargs["settings"]


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5-2025-08-07",
        "gpt-5-mini-2025-08-07",
    ],
)
def test_first_gpt5_releases_send_minimal_effort(model):
    # These releases reject "none".
    assert _settings(model).extra == {
        "reasoning_effort": "minimal",
        "verbosity": "low",
    }


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.1",
        "gpt-5.4-mini",
        "gpt-5.5-2026-04-23",
        "gpt-5.6-terra",
    ],
)
def test_later_gpt5_releases_send_none_effort(model):
    # These reject "minimal", and accept function tools only with "none".
    assert _settings(model).extra == {
        "reasoning_effort": "none",
        "verbosity": "low",
    }


def test_provider_prefixed_gpt5_model_uses_its_base_name():
    assert _settings("openai/gpt-5-mini", provider="atlascloud").extra == {
        "reasoning_effort": "minimal",
        "verbosity": "low",
    }


def test_non_gpt5_model_sends_no_reasoning_effort():
    settings = _settings("gpt-4.1")

    assert "reasoning_effort" not in (settings.extra or {})
    assert settings.temperature == 0.1
