"""Provider-neutral fallback policy validation, construction and run counters."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pipecat.services.llm_service import LLMService
from pydantic import ValidationError

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.llm_fallback import FallbackPolicy, NoOutputCondition
from api.schemas.model_connections import ServiceSelection
from api.services.configuration.registry import REGISTRY, ServiceType
from api.services.pipecat.fallback_llm import FallbackLLMProcessor
from api.services.pipecat.service_factory import (
    create_llm_service,
    get_llm_runtime_configuration,
)
from api.services.workflow.agent_runtime import AgentRuntime
from api.services.workflow.pipecat_engine import PipecatEngine


@pytest.mark.parametrize("delay", [99, 10001, None, 1500.5])
def test_delay_rejects_invalid_values(delay):
    with pytest.raises(ValidationError):
        NoOutputCondition(after_ms=delay)


@pytest.mark.parametrize("delay", [100, 1500, 10000])
def test_delay_accepts_bounds(delay):
    assert NoOutputCondition(after_ms=delay).after_ms == delay


@pytest.mark.parametrize(
    "conditions",
    [
        [{"type": "error"}, {"type": "error"}],
        [{"type": "context_window"}],
        [{"type": "error", "status_codes": [429]}],
    ],
)
def test_only_supported_unique_conditions_are_accepted(conditions):
    with pytest.raises(ValidationError):
        FallbackPolicy[ServiceSelection].model_validate(
            {
                "rules": [
                    {
                        "condition": condition,
                        "target": {
                            "provider_connection_uuid": "11111111-1111-4111-8111-111111111111"
                        },
                    }
                    for condition in conditions
                ]
            }
        )


def configuration(*, error_target=None, slow_target=None):
    return EffectiveAIModelConfiguration.model_validate(
        {
            "llm": {
                "provider": "google",
                "api_key": "primary-key",
                "model": "gemini-3.5-flash",
                "temperature": 0.3,
            },
            "llm_fallback": {
                "version": 1,
                "rules": [
                    *(
                        [
                            {
                                "condition": {"type": "no_output", "after_ms": 850},
                                "target": slow_target,
                            }
                        ]
                        if slow_target
                        else []
                    ),
                    *(
                        [{"condition": {"type": "error"}, "target": error_target}]
                        if error_target
                        else []
                    ),
                ],
            },
        }
    )


def test_independent_connections_and_targets_reach_factory():
    config = configuration(
        slow_target={
            "provider": "openai",
            "api_key": "backup-key",
            "model": "gpt-4.1",
            "temperature": 0.8,
        },
        error_target={
            "provider": "google_vertex",
            "project_id": "backup-project",
            "credentials": "secret",
            "location": "global",
        },
    )
    with (
        patch("api.services.pipecat.service_factory.DograhGoogleLLMService") as google,
        patch("api.services.pipecat.service_factory.OpenAILLMService") as openai,
        patch(
            "api.services.pipecat.service_factory.DograhGoogleVertexLLMService"
        ) as vertex,
        patch("api.services.pipecat.service_factory.FallbackLLMProcessor") as composite,
    ):
        create_llm_service(config)
    assert google.call_args.kwargs["api_key"] == "primary-key"
    assert openai.call_args.kwargs["api_key"] == "backup-key"
    assert openai.call_args.kwargs["settings"].temperature == 0.8
    assert vertex.call_args.kwargs["project_id"] == "backup-project"
    assert vertex.call_args.kwargs["credentials"] == "secret"
    assert vertex.call_args.kwargs["location"] == "global"
    assert all(
        constructor.call_args.kwargs["enable_direct_mode"]
        for constructor in (google, openai, vertex)
    )
    assert [route.condition.type for route in composite.call_args.kwargs["routes"]] == [
        "no_output",
        "error",
    ]


def test_identical_rule_targets_share_one_service():
    target = {"provider": "openai", "api_key": "test", "model": "gpt-4.1"}
    with (
        patch("api.services.pipecat.service_factory.DograhGoogleLLMService"),
        patch("api.services.pipecat.service_factory.OpenAILLMService") as openai,
        patch("api.services.pipecat.service_factory.FallbackLLMProcessor") as composite,
    ):
        create_llm_service(configuration(slow_target=target, error_target=target))
    openai.assert_called_once()
    routes = composite.call_args.kwargs["routes"]
    assert routes[0].service is routes[1].service


def test_no_rules_returns_ordinary_service():
    with patch(
        "api.services.pipecat.service_factory.DograhGoogleLLMService"
    ) as constructor:
        result = create_llm_service(configuration())
    assert result is constructor.return_value
    assert constructor.call_args.kwargs["enable_direct_mode"] is False


def test_effective_configuration_rejects_dograh_primary_fallbacks():
    config = configuration(error_target={"provider": "openai", "api_key": "backup"})
    payload = config.model_dump(exclude_computed_fields=True)
    payload["llm"] = {"provider": "dograh", "api_key": "primary"}
    with pytest.raises(ValidationError, match="non-Dograh primary LLM"):
        EffectiveAIModelConfiguration.model_validate(payload)


@pytest.mark.parametrize("trigger", ["slow_target", "error_target"])
def test_effective_configuration_rejects_dograh_fallback_targets(trigger):
    with pytest.raises(ValidationError, match="cannot be a fallback target"):
        configuration(**{trigger: {"provider": "dograh", "api_key": "managed"}})


def test_realtime_retains_fallbacks_for_its_separate_text_llm():
    config = configuration(error_target={"provider": "openai", "api_key": "backup"})
    payload = config.model_dump(exclude_computed_fields=True)
    payload["is_realtime"] = True
    payload["realtime"] = {"provider": "openai_realtime", "api_key": "audio"}
    config = EffectiveAIModelConfiguration.model_validate(payload)
    with patch(
        "api.services.pipecat.service_factory._create_single_llm_service",
        side_effect=[
            LLMService(enable_direct_mode=True),
            LLMService(enable_direct_mode=True),
        ],
    ):
        assert isinstance(create_llm_service(config), FallbackLLMProcessor)


def test_runtime_metadata_omits_credentials_and_records_conditions():
    config = configuration(
        error_target={
            "provider": "google_vertex",
            "project_id": "private-project",
            "credentials": "secret",
            "location": "global",
        }
    )
    metadata = get_llm_runtime_configuration(config)
    assert metadata["llm_fallback"]["rules"][0] == {
        "condition": {"type": "error"},
        "target": {
            "provider": "google_vertex",
            "model": "gemini-3.5-flash",
            "location": "global",
        },
    }
    assert "secret" not in str(metadata) and "primary-key" not in str(metadata)


def test_registry_has_no_provider_specific_fallback_fields():
    for cls in REGISTRY[ServiceType.LLM].values():
        assert not any(field.startswith("fallback_") for field in cls.model_fields)


@pytest.mark.asyncio
async def test_vertex_connections_keep_independent_clients_and_close_them():
    from pipecat.services.google.vertex.llm import GoogleVertexLLMService

    credentials = object()
    clients = [MagicMock(), MagicMock()]
    for client in clients:
        client.aio.aclose = AsyncMock()
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "llm": {
                "provider": "google_vertex",
                "project_id": "primary-project",
                "location": "eu",
            },
            "llm_fallback": {
                "rules": [
                    {
                        "condition": {"type": "error"},
                        "target": {
                            "provider": "google_vertex",
                            "project_id": "backup-project",
                            "location": "global",
                        },
                    }
                ]
            },
        }
    )
    with (
        patch.object(
            GoogleVertexLLMService, "_get_credentials", return_value=credentials
        ),
        patch(
            "pipecat.services.google.vertex.llm.Client", side_effect=clients
        ) as create,
    ):
        service = create_llm_service(config)
    assert isinstance(service, FallbackLLMProcessor)
    assert [call.kwargs["location"] for call in create.call_args_list] == [
        "eu",
        "global",
    ]
    assert [call.kwargs["project"] for call in create.call_args_list] == [
        "primary-project",
        "backup-project",
    ]
    await service.cleanup()
    for client in clients:
        client.aio.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_counters_sum_retired_and_active_visits():
    engine = object.__new__(PipecatEngine)
    engine._gathered_context = {"existing": "value"}
    engine._agent_visits = [{"llm_fallback_metrics": {"started": 2, "won": 1}}]
    agent = AgentRuntime(
        visit_id="active",
        workflow_id=1,
        definition_id=1,
        workflow_name="test",
        workflow=SimpleNamespace(),
        llm=SimpleNamespace(fallback_metrics={"started": 3, "won": 2}),
        inference_llm=None,
        variable_extraction_llm=None,
    )
    engine._active_agent = agent
    first = await engine.get_gathered_context()
    assert first["llm_fallback_metrics"] == {"started": 5, "won": 3}
    assert await engine.get_gathered_context() == first
    assert engine._gathered_context == {"existing": "value"}

    engine._gathered_context["llm_fallback_metrics"] = {"started": 4, "won": 2}
    assert (await engine.get_gathered_context())["llm_fallback_metrics"] == {
        "started": 9,
        "won": 5,
    }
