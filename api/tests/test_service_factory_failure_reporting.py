from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.errors.failure import ErrorSource, ErrorType, failure_metadata_for_processor
from api.services.pipecat.service_factory import create_llm_service_from_provider


def test_service_factory_classifies_constructor_failure_and_reraises(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "api.services.pipecat.service_factory.log_failure",
        lambda failure: captured.append(failure),
    )

    with pytest.raises(HTTPException):
        create_llm_service_from_provider(
            provider="not-a-provider",
            model="model",
            api_key="key",
        )

    assert captured[0].source == ErrorSource.LLM
    assert captured[0].type == ErrorType.CONFIG_ERROR
    assert captured[0].code == "not-a-provider-400"
    assert captured[0].error_owner.value == "user"


def test_service_factory_tags_success_with_authoritative_ownership():
    service = SimpleNamespace()
    with patch(
        "api.services.pipecat.service_factory.OpenAILLMService",
        return_value=service,
    ):
        result = create_llm_service_from_provider(
            provider="openai",
            model="gpt-4.1-mini",
            api_key="key",
        )

    metadata = failure_metadata_for_processor(result)
    assert metadata.source == ErrorSource.LLM
    assert metadata.provider == "openai"
    assert metadata.error_owner.value == "user"


def test_managed_service_constructor_failure_is_attributed_to_dograh(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "api.services.pipecat.service_factory.log_failure",
        lambda failure: captured.append(failure),
    )
    with (
        patch(
            "api.services.pipecat.service_factory.DograhLLMService",
            side_effect=ValueError("bad managed response"),
        ),
        pytest.raises(ValueError),
    ):
        create_llm_service_from_provider(
            provider="dograh",
            model="gpt-4.1-mini",
            api_key="managed-key",
        )

    assert captured[0].type == ErrorType.SYSTEM_ERROR
    assert captured[0].error_owner.value == "operator"
