"""Gemini fallback configuration, construction and persisted call counters."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from api.schemas.ai_model_configuration import (
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration.ai_model_configuration import (
    get_effective_ai_model_configuration_for_workflow,
)
from api.services.configuration.registry import (
    GoogleLLMService,
    GoogleRealtimeLLMConfiguration,
    GoogleVertexLLMConfiguration,
    GoogleVertexRealtimeLLMConfiguration,
)
from api.services.pipecat.fallback_llm import FallbackLLMProcessor
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_llm_service_from_provider,
    get_llm_runtime_configuration,
)
from api.services.workflow.agent_runtime import AgentRuntime
from api.services.workflow.pipecat_engine import PipecatEngine


@pytest.mark.parametrize(
    "config_cls,required",
    [
        (GoogleLLMService, {"api_key": "test"}),
        (GoogleVertexLLMConfiguration, {"project_id": "test"}),
    ],
)
def test_fallback_config_defaults_and_roundtrip(config_cls, required):
    config = config_cls(**required)
    assert config.fallback_model is None
    assert config.fallback_after_ms == 1500
    config = config_cls(
        **required, fallback_model=" gemini-3.1-flash-lite ", fallback_after_ms=700
    )
    assert config.fallback_model == "gemini-3.1-flash-lite"
    assert config_cls.model_validate_json(config.model_dump_json()) == config
    assert config_cls(**required, fallback_model="  ").fallback_model is None


@pytest.mark.parametrize("delay", [299, 10001, None, 1500.5])
def test_fallback_delay_rejects_out_of_range_or_non_integer(delay):
    with pytest.raises(ValidationError):
        GoogleVertexLLMConfiguration(project_id="test", fallback_after_ms=delay)


@pytest.mark.parametrize("delay", [300, 1500, 10000])
def test_fallback_delay_accepts_bounds(delay):
    assert (
        GoogleVertexLLMConfiguration(
            project_id="test", fallback_after_ms=delay
        ).fallback_after_ms
        == delay
    )


def test_vertex_location_normalizes_blank_to_none():
    assert (
        GoogleVertexLLMConfiguration(
            project_id="test", fallback_location="  "
        ).fallback_location
        is None
    )
    assert (
        GoogleVertexLLMConfiguration(
            project_id="test", fallback_location=" eu "
        ).fallback_location
        == "eu"
    )


def test_fallback_fields_are_scoped_to_non_realtime_gemini():
    assert "fallback_location" not in GoogleLLMService.model_fields
    for config in (
        GoogleRealtimeLLMConfiguration,
        GoogleVertexRealtimeLLMConfiguration,
    ):
        assert not any(name.startswith("fallback_") for name in config.model_fields)


@pytest.mark.parametrize(
    "provider,service,required",
    [
        ("google", "DograhGoogleLLMService", {"api_key": "test"}),
        (
            "google_vertex",
            "DograhGoogleVertexLLMService",
            {
                "api_key": None,
                "project_id": "test",
                "location": "eu",
                "fallback_location": "global",
            },
        ),
    ],
)
def test_direct_factory_forwards_fallback(provider, service, required):
    with (
        patch(f"api.services.pipecat.service_factory.{service}") as constructor,
        patch("api.services.pipecat.service_factory.FallbackLLMProcessor") as composite,
    ):
        create_llm_service_from_provider(
            provider,
            "gemini-3.5-flash",
            **required,
            fallback_model="gemini-3.1-flash-lite",
            fallback_after_ms=750,
        )
    assert constructor.call_count == 2
    primary, backup = [call.kwargs for call in constructor.call_args_list]
    assert primary["settings"].model == "gemini-3.5-flash"
    assert backup["settings"].model == "gemini-3.1-flash-lite"
    assert primary["enable_direct_mode"] and backup["enable_direct_mode"]
    assert composite.call_args.kwargs["fallback_after_secs"] == 0.75
    if provider == "google_vertex":
        assert primary["location"] == "eu"
        assert backup["location"] == "global"
        assert primary["project_id"] == backup["project_id"] == "test"
        assert primary["credentials"] == backup["credentials"]


@pytest.mark.parametrize(
    "config_cls,service,required",
    [
        (GoogleLLMService, "DograhGoogleLLMService", {"api_key": "test"}),
        (
            GoogleVertexLLMConfiguration,
            "DograhGoogleVertexLLMService",
            {"project_id": "test", "fallback_location": "global", "location": "eu"},
        ),
    ],
)
def test_resolved_config_factory_forwards_fallback(config_cls, service, required):
    config = config_cls(
        **required, fallback_model="gemini-3.1-flash-lite", fallback_after_ms=850
    )
    with (
        patch(f"api.services.pipecat.service_factory.{service}") as constructor,
        patch("api.services.pipecat.service_factory.FallbackLLMProcessor") as composite,
    ):
        create_llm_service(SimpleNamespace(llm=config))
    assert constructor.call_count == 2
    kwargs = constructor.call_args.kwargs
    assert kwargs["settings"].model == config.fallback_model
    assert composite.call_args.kwargs["fallback_after_secs"] == 0.85
    if config_cls is GoogleVertexLLMConfiguration:
        assert kwargs["location"] == "global"


def test_runtime_configuration_records_fallback_without_secrets():
    config = GoogleVertexLLMConfiguration(
        project_id="test",
        credentials="secret",
        location="eu",
        fallback_location="global",
    )
    assert get_llm_runtime_configuration(config) == {
        "llm_provider": "google_vertex",
        "llm_model": "gemini-3.5-flash",
        "llm_location": "eu",
        "llm_fallback_location": "global",
        "llm_fallback_model": None,
        "llm_fallback_after_ms": 1500,
    }


def test_direct_factory_blank_fallback_location_stays_disabled():
    with patch(
        "api.services.pipecat.service_factory.DograhGoogleVertexLLMService"
    ) as constructor:
        create_llm_service_from_provider(
            "google_vertex",
            "gemini-3.5-flash",
            None,
            project_id="test",
            location="eu",
            fallback_location="  ",
        )
    constructor.assert_called_once()
    assert "fallback_location" not in constructor.call_args.kwargs
    assert "enable_direct_mode" not in constructor.call_args.kwargs


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


@pytest.mark.asyncio
async def test_v2_org_and_full_workflow_override_preserve_fallback():
    payload = {
        "mode": "byok",
        "byok": {
            "mode": "pipeline",
            "pipeline": {
                "llm": {
                    "provider": "google_vertex",
                    "project_id": "test",
                    "location": "eu",
                    "fallback_location": "global",
                    "fallback_after_ms": 900,
                },
                "stt": {"provider": "deepgram", "api_key": "test"},
                "tts": {"provider": "elevenlabs", "api_key": "test"},
            },
        },
    }
    config = OrganizationAIModelConfigurationV2.model_validate(payload)
    org = compile_ai_model_configuration_v2(config)
    workflow = await get_effective_ai_model_configuration_for_workflow(
        organization_id=1,
        workflow_configurations={"model_configuration_v2_override": payload},
    )
    assert org.llm.fallback_location == workflow.llm.fallback_location == "global"
    assert org.llm.fallback_after_ms == workflow.llm.fallback_after_ms == 900


@pytest.mark.parametrize(
    "fallback_model,fallback_location",
    [(None, None), ("gemini-3.5-flash", "eu"), (" ", " ")],
)
def test_matching_or_empty_fallback_returns_single_unchanged_service(
    fallback_model, fallback_location
):
    with (
        patch(
            "api.services.pipecat.service_factory.DograhGoogleVertexLLMService"
        ) as constructor,
        patch("api.services.pipecat.service_factory.FallbackLLMProcessor") as composite,
    ):
        result = create_llm_service_from_provider(
            "google_vertex",
            "gemini-3.5-flash",
            None,
            project_id="test",
            location="eu",
            fallback_model=fallback_model,
            fallback_location=fallback_location,
        )
    constructor.assert_called_once()
    composite.assert_not_called()
    assert result is constructor.return_value
    assert "enable_direct_mode" not in constructor.call_args.kwargs


@pytest.mark.asyncio
async def test_vertex_location_only_composes_existing_services_and_closes_clients():
    from pipecat.services.google.vertex.llm import GoogleVertexLLMService

    credentials = object()
    clients = [MagicMock(), MagicMock()]
    for client in clients:
        client.aio.aclose = AsyncMock()
    with (
        patch.object(
            GoogleVertexLLMService, "_get_credentials", return_value=credentials
        ),
        patch(
            "pipecat.services.google.vertex.llm.Client", side_effect=clients
        ) as create,
    ):
        service = create_llm_service_from_provider(
            "google_vertex",
            "gemini-3.5-flash",
            None,
            project_id="test",
            location="eu",
            fallback_location="global",
        )
    assert isinstance(service, FallbackLLMProcessor)
    assert isinstance(service.primary, GoogleVertexLLMService)
    assert isinstance(service.fallback, GoogleVertexLLMService)
    assert (
        service.primary.settings.model
        == service.fallback.settings.model
        == "gemini-3.5-flash"
    )
    assert [c.kwargs["location"] for c in create.call_args_list] == ["eu", "global"]
    assert all(c.kwargs["credentials"] is credentials for c in create.call_args_list)
    assert all(c.kwargs["project"] == "test" for c in create.call_args_list)
    await service.cleanup()
    for client in clients:
        client.aio.aclose.assert_awaited_once()
