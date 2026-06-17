from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    REGISTRY,
    HuggingFaceLLMConfiguration,
    HuggingFaceSTTConfiguration,
    ServiceProviders,
    ServiceType,
)
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_stt_service,
)


def test_huggingface_stt_configuration_defaults_and_registry():
    config = HuggingFaceSTTConfiguration(api_key="hf_test")

    assert config.provider == ServiceProviders.HUGGINGFACE
    assert config.model == "openai/whisper-large-v3-turbo"
    assert config.base_url == "https://router.huggingface.co/hf-inference"
    assert config.return_timestamps is False
    assert (
        REGISTRY[ServiceType.STT][ServiceProviders.HUGGINGFACE]
        is HuggingFaceSTTConfiguration
    )


def test_huggingface_llm_configuration_defaults_and_registry():
    config = HuggingFaceLLMConfiguration(api_key="hf_test")

    assert config.provider == ServiceProviders.HUGGINGFACE
    assert config.model == "openai/gpt-oss-120b:cerebras"
    assert config.base_url == "https://router.huggingface.co/v1"
    assert (
        REGISTRY[ServiceType.LLM][ServiceProviders.HUGGINGFACE]
        is HuggingFaceLLMConfiguration
    )


def test_create_huggingface_llm_service_uses_openai_compatible_router():
    user_config = SimpleNamespace(
        llm=SimpleNamespace(
            provider=ServiceProviders.HUGGINGFACE.value,
            api_key="hf_test",
            model="deepseek-ai/DeepSeek-R1:fastest",
            base_url="https://router.huggingface.co/v1",
            bill_to="demo-org",
        )
    )

    with patch(
        "api.services.pipecat.service_factory.HuggingFaceLLMService"
    ) as mock_service:
        create_llm_service(user_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "hf_test"
    assert kwargs["base_url"] == "https://router.huggingface.co/v1"
    assert kwargs["bill_to"] == "demo-org"
    assert kwargs["settings"].model == "deepseek-ai/DeepSeek-R1:fastest"
    assert kwargs["settings"].temperature == 0.1


def test_create_huggingface_stt_service_uses_hosted_defaults():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.HUGGINGFACE.value,
            api_key="hf_test",
            model="openai/whisper-large-v3-turbo",
            base_url="https://router.huggingface.co/hf-inference",
            bill_to="demo-org",
            return_timestamps=True,
        )
    )
    audio_config = SimpleNamespace(transport_in_sample_rate=16000)

    with patch(
        "api.services.pipecat.service_factory.HuggingFaceSTTService"
    ) as mock_service:
        create_stt_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "hf_test"
    assert kwargs["base_url"] == "https://router.huggingface.co/hf-inference"
    assert kwargs["bill_to"] == "demo-org"
    assert kwargs["sample_rate"] == 16000
    assert kwargs["settings"].model == "openai/whisper-large-v3-turbo"
    assert kwargs["settings"].return_timestamps is True


def test_validator_accepts_huggingface_stt_token_format():
    validator = UserConfigurationValidator()

    assert (
        validator._validate_service(
            HuggingFaceSTTConfiguration(api_key="hf_test"),
            "stt",
        )
        == []
    )
    assert (
        validator._validate_service(
            HuggingFaceLLMConfiguration(api_key="hf_test"),
            "llm",
        )
        == []
    )


def test_validator_rejects_non_huggingface_token_format():
    validator = UserConfigurationValidator()

    errors = validator._validate_service(
        HuggingFaceSTTConfiguration(api_key="not-hf-token"),
        "stt",
    )

    assert errors == [
        {
            "model": "stt",
            "message": (
                "Invalid Hugging Face API token format. Use a token that starts with "
                "'hf_' and has Inference Providers permission."
            ),
        }
    ]
