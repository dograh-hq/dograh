"""Tests for centralized QA LLM service creation (#527)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import (
    GoogleVertexLLMConfiguration,
    HuggingFaceLLMConfiguration,
)
from api.services.configuration.registry import (
    OpenAILLMService as OpenAILLMConfiguration,
)
from api.services.pipecat.service_factory import (
    create_llm_service_with_model_override,
)
from api.services.workflow.qa.llm_config import create_qa_llm_service

_CONFIG_FN = (
    "api.services.workflow.qa.llm_config"
    ".get_effective_ai_model_configuration_for_workflow"
)
_OWN_LLM_FACTORY = (
    "api.services.workflow.qa.llm_config.create_llm_service_from_provider"
)
_WORKFLOW_LLM_FACTORY = (
    "api.services.workflow.qa.llm_config.create_llm_service_with_model_override"
)


def _qa(**overrides):
    data = dict(
        qa_use_workflow_llm=False,
        qa_provider="openai",
        qa_endpoint=None,
        qa_model="m",
        qa_api_key="k",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _workflow_run(
    *,
    workflow_configurations=None,
    definition_configurations=None,
    initial_context=None,
):
    definition = (
        SimpleNamespace(workflow_configurations=definition_configurations)
        if definition_configurations is not None
        else None
    )
    return SimpleNamespace(
        workflow=SimpleNamespace(
            organization_id=1,
            workflow_configurations=workflow_configurations or {},
        ),
        definition=definition,
        initial_context=initial_context or {},
    )


@pytest.mark.asyncio
async def test_qa_own_llm_uses_explicit_provider_factory():
    qa = _qa(
        qa_provider="openai",
        qa_model="grok-3-fast",
        qa_api_key="xai-key",
    )
    service = object()
    factory = Mock(return_value=service)

    with patch(_OWN_LLM_FACTORY, factory):
        result = await create_qa_llm_service(qa, None)

    assert result == (service, "grok-3-fast")
    factory.assert_called_once_with(
        "openai",
        "grok-3-fast",
        "xai-key",
        correlation_id=None,
        usage_context="qa_analysis",
    )


@pytest.mark.asyncio
async def test_qa_own_llm_azure_forwards_endpoint_and_correlation_id():
    qa = _qa(qa_provider="azure", qa_endpoint="https://x.openai.azure.com")
    run = SimpleNamespace(initial_context={"mps_correlation_id": "corr-123"})
    service = object()
    factory = Mock(return_value=service)

    with patch(_OWN_LLM_FACTORY, factory):
        result = await create_qa_llm_service(qa, run)

    assert result == (service, "m")
    assert factory.call_args.kwargs == {
        "correlation_id": "corr-123",
        "usage_context": "qa_analysis",
        "endpoint": "https://x.openai.azure.com",
    }


@pytest.mark.asyncio
async def test_qa_own_llm_without_api_key_returns_none():
    factory = Mock()
    with patch(_OWN_LLM_FACTORY, factory):
        result = await create_qa_llm_service(_qa(qa_api_key=None), None)

    assert result is None
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_llm_delegates_typed_config_to_central_factory():
    config = EffectiveAIModelConfiguration(
        llm=OpenAILLMConfiguration(
            api_key="xai-key",
            model="grok-3-fast",
            base_url="https://api.x.ai/v1",
        )
    )
    run = _workflow_run(
        workflow_configurations={"source": "workflow"},
        initial_context={"mps_correlation_id": "corr-123"},
    )
    service = object()
    config_resolver = AsyncMock(return_value=config)
    factory = Mock(return_value=service)

    with (
        patch(_CONFIG_FN, config_resolver),
        patch(_WORKFLOW_LLM_FACTORY, factory),
    ):
        result = await create_qa_llm_service(
            _qa(qa_use_workflow_llm=True, qa_model="default"),
            run,
        )

    assert result == (service, "grok-3-fast")
    config_resolver.assert_awaited_once_with(
        organization_id=1,
        workflow_configurations={"source": "workflow"},
    )
    factory.assert_called_once_with(
        config,
        None,
        correlation_id="corr-123",
        usage_context="qa_analysis",
    )
    assert factory.call_args.args[0].llm.base_url == "https://api.x.ai/v1"


@pytest.mark.asyncio
async def test_workflow_llm_applies_qa_model_override():
    config = EffectiveAIModelConfiguration(
        llm=OpenAILLMConfiguration(api_key="k", model="gpt-4.1")
    )
    service = object()
    factory = Mock(return_value=service)

    with (
        patch(_CONFIG_FN, AsyncMock(return_value=config)),
        patch(_WORKFLOW_LLM_FACTORY, factory),
    ):
        result = await create_qa_llm_service(
            _qa(qa_use_workflow_llm=True, qa_model="gpt-5-mini"),
            _workflow_run(definition_configurations={"source": "definition"}),
        )

    assert result == (service, "gpt-5-mini")
    factory.assert_called_once_with(
        config,
        "gpt-5-mini",
        correlation_id=None,
        usage_context="qa_analysis",
    )


@pytest.mark.asyncio
async def test_workflow_without_llm_configuration_returns_none():
    factory = Mock()
    with (
        patch(_CONFIG_FN, AsyncMock(return_value=EffectiveAIModelConfiguration())),
        patch(_WORKFLOW_LLM_FACTORY, factory),
    ):
        result = await create_qa_llm_service(
            _qa(qa_use_workflow_llm=True),
            _workflow_run(),
        )

    assert result is None
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_llm_supports_provider_without_api_key():
    config = EffectiveAIModelConfiguration(
        llm=GoogleVertexLLMConfiguration(
            project_id="demo-project",
            location="us-east4",
            credentials='{"type":"service_account"}',
        )
    )

    with (
        patch(_CONFIG_FN, AsyncMock(return_value=config)),
        patch(
            "api.services.pipecat.service_factory.DograhGoogleVertexLLMService"
        ) as vertex_service,
    ):
        result = await create_qa_llm_service(
            _qa(qa_use_workflow_llm=True, qa_model="default"),
            _workflow_run(),
        )

    assert result == (vertex_service.return_value, "gemini-3.5-flash")
    kwargs = vertex_service.call_args.kwargs
    assert kwargs["project_id"] == "demo-project"
    assert kwargs["location"] == "us-east4"
    assert kwargs["credentials"] == '{"type":"service_account"}'


def test_model_override_wrapper_preserves_openai_base_url():
    config = EffectiveAIModelConfiguration(
        llm=OpenAILLMConfiguration(
            api_key="xai-key",
            model="configured-model",
            base_url="https://api.x.ai/v1",
        )
    )

    with patch(
        "api.services.pipecat.service_factory.OpenAILLMService"
    ) as openai_service:
        result = create_llm_service_with_model_override(
            config,
            "grok-3-fast",
            correlation_id="corr-123",
            usage_context="qa_analysis",
        )

    assert result is openai_service.return_value
    assert config.llm.model == "configured-model"
    kwargs = openai_service.call_args.kwargs
    assert kwargs["base_url"] == "https://api.x.ai/v1"
    assert kwargs["settings"].model == "grok-3-fast"


def test_model_override_wrapper_preserves_provider_specific_fields():
    config = EffectiveAIModelConfiguration(
        llm=HuggingFaceLLMConfiguration(
            api_key="hf-key",
            model="configured-model",
            base_url="https://router.huggingface.co/v1",
            bill_to="billing-org",
        )
    )
    service = object()

    with patch(
        "api.services.pipecat.service_factory.create_llm_service",
        return_value=service,
    ) as central_factory:
        result = create_llm_service_with_model_override(config, "qa-model")

    assert result is service
    delegated_config = central_factory.call_args.args[0]
    assert delegated_config is not config
    assert delegated_config.llm.model == "qa-model"
    assert delegated_config.llm.base_url == "https://router.huggingface.co/v1"
    assert delegated_config.llm.bill_to == "billing-org"
    assert config.llm.model == "configured-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
@pytest.mark.parametrize("override", ["default", "gpt-5.6-sol"])
async def test_subscription_workflow_qa_uses_owned_client(outcome, override):
    import asyncio

    from api.errors.failure import ErrorSource, failure_metadata_for_processor
    from api.services.configuration.registry import (
        OpenAILiveSubscriptionLLMConfiguration,
    )

    config = EffectiveAIModelConfiguration(
        is_realtime=True, realtime=OpenAILiveSubscriptionLLMConfiguration()
    )
    service = SimpleNamespace(
        run_inference=AsyncMock(return_value="subscription answer"),
        aclose=AsyncMock(),
    )
    if outcome == "failure":
        service.run_inference.side_effect = RuntimeError("subscription unavailable")
    elif outcome == "cancelled":
        service.run_inference.side_effect = asyncio.CancelledError()
    with (
        patch(_CONFIG_FN, AsyncMock(return_value=config)),
        patch(_OWN_LLM_FACTORY) as api_factory,
        patch(_WORKFLOW_LLM_FACTORY) as workflow_api_factory,
        patch(
            "api.services.workflow.qa.llm_config.create_subscription_inference_service",
            return_value=service,
        ) as subscription_factory,
    ):
        llm, model = await create_qa_llm_service(
            _qa(qa_use_workflow_llm=True, qa_model=override, qa_api_key=None),
            _workflow_run(),
        )
        subscription_factory.assert_not_called()
        assert model == ("gpt-5.6-luna" if override == "default" else override)
        assert failure_metadata_for_processor(llm).source == ErrorSource.LLM
        context = object()
        if outcome == "success":
            assert (
                await llm.run_inference(context, system_instruction="QA")
                == "subscription answer"
            )
        else:
            expected = RuntimeError if outcome == "failure" else asyncio.CancelledError
            with pytest.raises(expected):
                await llm.run_inference(context, system_instruction="QA")
        subscription_factory.assert_called_once_with(
            config,
            organization_id=1,
            model_override=None if override == "default" else override,
        )
        service.run_inference.assert_awaited_once_with(context, system_instruction="QA")
        service.aclose.assert_awaited_once()
        api_factory.assert_not_called()
        workflow_api_factory.assert_not_called()
