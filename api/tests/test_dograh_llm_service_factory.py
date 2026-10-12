from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.service_factory import create_llm_service


@pytest.mark.parametrize("model", ["default", "fast", "zen"])
def test_create_dograh_llm_service_passes_model_and_usage_context(model):
    user_config = SimpleNamespace(
        llm=SimpleNamespace(
            provider=ServiceProviders.DOGRAH.value,
            api_key="mps-key",
            model=model,
        )
    )

    with patch(
        "api.services.pipecat.service_factory.DograhLLMService"
    ) as dograh_llm_service:
        create_llm_service(
            user_config,
            correlation_id="corr-123",
            usage_context="variable_extraction",
        )

    kwargs = dograh_llm_service.call_args.kwargs
    assert kwargs["settings"].model == model
    assert kwargs["correlation_id"] == "corr-123"
    assert kwargs["usage_context"] == "variable_extraction"
