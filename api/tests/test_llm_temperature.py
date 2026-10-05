"""Exercise saved settings all the way to provider request construction."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from google.auth.credentials import AnonymousCredentials
from openai import NotGiven as OpenAINotGiven
from pydantic import ValidationError

from api.schemas.ai_model_configuration import (
    EffectiveAIModelConfiguration,
    compile_ai_model_configuration_v2,
)
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
)
from api.services.configuration.registry import (
    AWS_BEDROCK_MODELS,
    REGISTRY,
    ServiceType,
)
from api.services.configuration.resolve import resolve_effective_config
from api.services.pipecat.service_factory import (
    DograhGoogleVertexLLMService,
    create_llm_service,
    create_llm_service_from_provider,
    create_llm_service_with_model_override,
    create_realtime_llm_service,
)

PROVIDERS = list(REGISTRY[ServiceType.LLM])


def _config(provider, **overrides):
    return REGISTRY[ServiceType.LLM][provider](
        **{
            "api_key": "test-key",
            "project_id": "test-project",
            "endpoint": "https://test.openai.azure.com",
            "aws_access_key": "test-access-key",
            "aws_secret_key": "test-secret-key",
            **overrides,
        }
    )


@pytest.fixture(autouse=True)
def anonymous_vertex_credentials(monkeypatch):
    monkeypatch.setattr(
        DograhGoogleVertexLLMService,
        "_get_credentials",
        staticmethod(lambda *_: AnonymousCredentials()),
    )


def _request_temperature(service):
    if hasattr(service, "build_chat_completion_params"):
        value = service.build_chat_completion_params({"messages": []})["temperature"]
        return None if isinstance(value, OpenAINotGiven) else value
    if hasattr(service, "_build_generation_params"):
        return service._build_generation_params().get("temperature")
    return service._build_inference_config().get("temperature")


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("temperature", [0.0, 0.73, None])
def test_saved_temperature_reaches_provider_request(provider, temperature):
    config = _config(provider, temperature=temperature)
    # Emulate persistence and reload through the discriminated request schema.
    saved = EffectiveAIModelConfiguration(llm=config).model_dump_json(exclude_none=True)
    reloaded = EffectiveAIModelConfiguration.model_validate_json(saved)
    service = create_llm_service(reloaded)
    assert _request_temperature(service) == temperature


@pytest.mark.parametrize("provider", PROVIDERS)
def test_omitted_temperature_preserves_existing_factory_defaults(provider):
    config = _config(provider)
    service = create_llm_service_from_provider(
        provider,
        config.model,
        config.api_key,
        endpoint="https://test.openai.azure.com",
        project_id="test-project",
        aws_access_key="test-access-key",
        aws_secret_key="test-secret-key",
    )
    expected = {
        "dograh": None,
        "speaches": None,
        "aws_bedrock": None,
        "minimax": 1.0,
        "sarvam": 0.5,
    }.get(provider, 0.1)
    assert _request_temperature(service) == expected


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("temperature", [-0.01, float("nan"), float("inf")])
def test_invalid_temperature_is_rejected(provider, temperature):
    with pytest.raises(ValidationError):
        _config(provider, temperature=temperature)


@pytest.mark.parametrize(
    "provider, model, maximum",
    [
        ("openai", "gpt-4.1", 2),
        ("groq", "openai/gpt-oss-120b", 2),
        ("google", "gemini-3.5-flash", 2),
        ("minimax", "MiniMax-M2.7", 2),
        ("minimax", "MiniMax-M3", 2),
        ("aws_bedrock", "us.amazon.nova-pro-v1:0", 1),
        ("openrouter", "anthropic/claude-sonnet-4", 1),
        ("openrouter", "openai/gpt-4.1", 2),
    ],
)
def test_model_specific_upper_bound(provider, model, maximum):
    assert _config(provider, model=model, temperature=maximum).temperature == maximum
    with pytest.raises(ValidationError):
        _config(provider, model=model, temperature=maximum + 0.01)


@pytest.mark.parametrize("model", AWS_BEDROCK_MODELS)
@pytest.mark.parametrize("temperature", [0, 0.73, 1])
def test_registered_bedrock_models_preserve_selected_temperature(model, temperature):
    config = _config("aws_bedrock", model=model, temperature=temperature)
    assert config.temperature == temperature
    assert (
        _request_temperature(create_llm_service(SimpleNamespace(llm=config)))
        == temperature
    )


def test_workflow_model_override_rejects_incompatible_inherited_temperature():
    config = EffectiveAIModelConfiguration(llm=_config("openrouter", temperature=1.5))
    with pytest.raises(ValidationError, match="between 0 and 1.0"):
        resolve_effective_config(
            config, {"llm": {"model": "anthropic/claude-sonnet-4"}}
        )
    assert config.llm.model == "openai/gpt-4.1"
    assert config.llm.temperature == 1.5


@pytest.mark.parametrize("temperature", [0, None])
def test_workflow_model_and_temperature_can_be_overridden_together(temperature):
    config = EffectiveAIModelConfiguration(llm=_config("openrouter", temperature=1.5))
    effective = resolve_effective_config(
        config,
        {
            "llm": {
                "model": "anthropic/claude-sonnet-4",
                "temperature": temperature,
            }
        },
    )
    assert _request_temperature(create_llm_service(effective)) == temperature


def test_temperature_serialization_respects_explicit_field_filters():
    config = _config("openai", temperature=None)
    assert "temperature" not in config.model_dump(
        exclude_none=True, exclude={"temperature"}
    )
    assert config.model_dump(exclude_none=True, include={"model"}) == {
        "model": "gpt-4.1"
    }


@pytest.mark.parametrize(
    "provider, model",
    [
        ("openai", "gpt-5"),
        ("openai", "gpt-5-mini-2025-08-07"),
        ("openai", "o3-mini"),
        ("azure", "gpt-5-nano"),
        ("azure", "gpt-5.4-mini"),
        ("azure", "gpt-5.4-nano-2026-03-17"),
        ("openrouter", "openai/gpt-5-mini"),
        ("openrouter", "openai/gpt-5.4-mini"),
        ("openrouter", "openai/gpt-5.4-mini-2026-03-17"),
        ("openrouter", "anthropic/claude-opus-4.7"),
        ("openrouter", "anthropic/claude-sonnet-5"),
        ("aws_bedrock", "us.anthropic.claude-opus-4-7-v1:0"),
        ("google", "gemini-3.6-flash"),
        ("google_vertex", "gemini-3.8-flash"),
    ],
)
def test_unsupported_models_omit_temperature_even_after_model_override(provider, model):
    user_config = EffectiveAIModelConfiguration(llm=_config(provider, temperature=0.5))
    service = create_llm_service_with_model_override(user_config, model)
    assert _request_temperature(service) is None


def test_workflow_override_preserves_zero_and_enforces_model_range():
    user_config = EffectiveAIModelConfiguration(
        llm=_config("openrouter", temperature=1.5)
    )
    overridden = resolve_effective_config(user_config, {"llm": {"temperature": 0}})
    assert _request_temperature(create_llm_service(overridden)) == 0
    with pytest.raises(HTTPException) as exc:
        create_llm_service_with_model_override(user_config, "anthropic/claude-sonnet-4")
    assert exc.value.status_code == 400
    assert "Temperature" in exc.value.detail
    assert "between 0 and 1.0" in exc.value.detail


@pytest.mark.parametrize("provider", ["azure", "openrouter"])
@pytest.mark.parametrize("model", ["gpt-5.1", "gpt-5.2", "gpt-5.4"])
def test_supported_numbered_gpt_models_keep_temperature(provider, model):
    if provider == "openrouter":
        model = f"openai/{model}"
    config = _config(provider, model=model, temperature=0.4)
    assert _request_temperature(create_llm_service(SimpleNamespace(llm=config))) == 0.4


@pytest.mark.parametrize(
    "base_url", ["http://localhost:11434/v1", "https://custom.example.com/v1"]
)
def test_custom_openai_endpoint_preserves_server_defined_upper_bound(base_url):
    config = _config("openai", base_url=base_url, model="llama3", temperature=3)
    saved = EffectiveAIModelConfiguration(llm=config).model_dump_json()
    reloaded = EffectiveAIModelConfiguration.model_validate_json(saved)
    assert _request_temperature(create_llm_service(reloaded)) == 3


@pytest.mark.parametrize(
    "base_url", ["https://api.openai.com/v1", "https://API.OPENAI.COM/v1/"]
)
def test_standard_openai_endpoint_enforces_upper_bound(base_url):
    with pytest.raises(ValidationError):
        _config("openai", base_url=base_url, temperature=3)
    with pytest.raises(HTTPException) as exc:
        create_llm_service_from_provider(
            "openai", "gpt-4.1", "test-key", base_url=base_url, temperature=3
        )
    assert exc.value.status_code == 400


def test_dograh_managed_conversion_keeps_temperature():
    legacy = EffectiveAIModelConfiguration(llm=_config("dograh", temperature=0))
    config = convert_legacy_ai_model_configuration_to_v2(legacy)
    assert config.dograh.temperature == 0
    assert (
        _request_temperature(
            create_llm_service(compile_ai_model_configuration_v2(config))
        )
        == 0
    )


@pytest.mark.parametrize("temperature", [0.0, 0.73, 1.0])
def test_ultravox_call_parameters_receive_temperature(temperature):
    config = REGISTRY[ServiceType.REALTIME]["ultravox_realtime"](
        api_key="test-key", temperature=temperature
    )
    service = create_realtime_llm_service(
        SimpleNamespace(realtime=config),
        SimpleNamespace(
            transport_in_sample_rate=16000, transport_out_sample_rate=24000
        ),
    )
    params = service._build_one_shot_params(
        greeting_text=None, agent_speaks_first=False
    )
    assert params.temperature == temperature


def test_realtime_temperature_schema_coverage():
    for provider, config in REGISTRY[ServiceType.REALTIME].items():
        assert ("temperature" in config.model_fields) == (
            provider in {"ultravox_realtime", "aws_nova_sonic"}
        )


def test_legacy_ultravox_config_preserves_existing_sdk_temperature():
    from api.services.pipecat.realtime.ultravox_realtime import (
        DograhUltravoxOneShotInputParams,
    )

    config = REGISTRY[ServiceType.REALTIME]["ultravox_realtime"](api_key="test-key")
    service = create_realtime_llm_service(
        SimpleNamespace(realtime=config),
        SimpleNamespace(
            transport_in_sample_rate=16000, transport_out_sample_rate=24000
        ),
    )
    params = service._build_one_shot_params(
        greeting_text=None, agent_speaks_first=False
    )
    # Before this PR the factory omitted temperature from the SDK constructor,
    # whose default was already serialized into every one-shot call request.
    old_params = DograhUltravoxOneShotInputParams(api_key="test-key")
    assert params.temperature == old_params.temperature == 0


def test_nova_sonic_zero_temperature_reaches_session_start():
    config = REGISTRY[ServiceType.REALTIME]["aws_nova_sonic"](
        aws_access_key="test-key", aws_secret_key="test-secret", temperature=0
    )
    service = create_realtime_llm_service(
        SimpleNamespace(realtime=config),
        SimpleNamespace(
            transport_in_sample_rate=16000, transport_out_sample_rate=24000
        ),
    )
    request = json.loads(service.build_session_start_json())
    assert (
        request["event"]["sessionStart"]["inferenceConfiguration"]["temperature"] == 0
    )
