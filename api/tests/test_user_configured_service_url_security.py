from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    ServiceProviders,
    SpeachesLLMConfiguration,
)
from api.services.gen_ai.embedding.openai_service import OpenAIEmbeddingService
from api.services.pipecat.service_factory import (
    create_llm_service_from_provider,
    create_stt_service,
    create_tts_service,
)
from api.utils.url_security import validate_user_configured_service_url


def test_oss_allows_local_service_urls(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "oss")

    validate_user_configured_service_url(
        "http://localhost:11434/v1",
        field_name="base_url",
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://10.0.0.10/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://100.64.0.1/v1",
    ],
)
def test_saas_blocks_local_and_internal_service_urls(url, monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    with pytest.raises(ValueError):
        validate_user_configured_service_url(
            url,
            field_name="base_url",
        )


def test_saas_rejects_unsupported_service_url_schemes(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    with pytest.raises(ValueError, match="http, https, ws, or wss"):
        validate_user_configured_service_url(
            "file:///etc/passwd",
            field_name="base_url",
        )


def test_saas_checks_resolved_hostname_ips(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    def fake_getaddrinfo(*_args, **_kwargs):
        return [(None, None, None, None, ("10.0.0.10", 443))]

    monkeypatch.setattr("api.utils.url_security.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValueError, match="public IP"):
        validate_user_configured_service_url(
            "https://internal.example.com/v1",
            field_name="base_url",
        )


def test_saas_allows_public_service_url(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    def fake_getaddrinfo(*_args, **_kwargs):
        return [(None, None, None, None, ("8.8.8.8", 443))]

    monkeypatch.setattr("api.utils.url_security.socket.getaddrinfo", fake_getaddrinfo)

    validate_user_configured_service_url(
        "https://api.example.com/v1",
        field_name="base_url",
    )


def test_saas_allows_public_websocket_service_url(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    def fake_getaddrinfo(*_args, **_kwargs):
        return [(None, None, None, None, ("8.8.8.8", 443))]

    monkeypatch.setattr("api.utils.url_security.socket.getaddrinfo", fake_getaddrinfo)

    validate_user_configured_service_url(
        "wss://api.example.com/v1",
        field_name="base_url",
    )


def test_saas_blocks_local_websocket_service_url(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    with pytest.raises(ValueError, match="localhost"):
        validate_user_configured_service_url(
            "ws://localhost:8000/v1",
            field_name="base_url",
        )


def test_validator_blocks_speaches_local_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    validator = UserConfigurationValidator()
    config = SpeachesLLMConfiguration()

    result = validator._validate_service(config, "llm")

    assert result == [
        {
            "model": "llm",
            "message": "base_url cannot point to localhost in SaaS mode",
        }
    ]


def test_validator_blocks_azure_private_endpoint_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    validator = UserConfigurationValidator()
    config = SimpleNamespace(
        provider=ServiceProviders.AZURE.value,
        endpoint="http://10.0.0.10/openai",
        api_key="test-key",
    )

    result = validator._validate_service(config, "llm")

    assert result == [
        {
            "model": "llm",
            "message": "endpoint must resolve to a public IP address in SaaS mode",
        }
    ]


def test_validator_allows_speaches_local_base_url_in_oss(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "oss")
    validator = UserConfigurationValidator()
    config = SpeachesLLMConfiguration()

    assert validator._validate_service(config, "llm") == []


def test_runtime_blocks_speaches_default_llm_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    with pytest.raises(HTTPException) as exc_info:
        create_llm_service_from_provider(
            provider=ServiceProviders.SPEACHES.value,
            model="llama3",
            api_key=None,
        )

    assert exc_info.value.status_code == 400
    assert "localhost" in exc_info.value.detail


def test_runtime_blocks_openai_private_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    with pytest.raises(HTTPException) as exc_info:
        create_llm_service_from_provider(
            provider=ServiceProviders.OPENAI.value,
            model="gpt-4.1",
            api_key="test-key",
            base_url="http://10.0.0.10/v1",
        )

    assert exc_info.value.status_code == 400
    assert "public IP" in exc_info.value.detail


def test_runtime_blocks_azure_private_endpoint_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    with pytest.raises(HTTPException) as exc_info:
        create_llm_service_from_provider(
            provider=ServiceProviders.AZURE.value,
            model="gpt-4.1-mini",
            api_key="test-key",
            endpoint="http://10.0.0.10/openai",
        )

    assert exc_info.value.status_code == 400
    assert "public IP" in exc_info.value.detail


def test_runtime_blocks_elevenlabs_local_tts_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.ELEVENLABS.value,
            api_key="test-key",
            model="eleven_flash_v2_5",
            voice="voice-id",
            speed=1.0,
            base_url="http://localhost:8000",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        create_tts_service(user_config, audio_config=None)

    assert exc_info.value.status_code == 400
    assert "localhost" in exc_info.value.detail


def test_runtime_blocks_openai_stt_private_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.OPENAI.value,
            api_key="test-key",
            model="gpt-4o-transcribe",
            base_url="http://10.0.0.10/v1",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        create_stt_service(user_config, audio_config=None)

    assert exc_info.value.status_code == 400
    assert "public IP" in exc_info.value.detail


def test_runtime_blocks_openai_stt_localhost_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.OPENAI.value,
            api_key="test-key",
            model="gpt-4o-transcribe",
            base_url="http://localhost:8000/v1",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        create_stt_service(user_config, audio_config=None)

    assert exc_info.value.status_code == 400
    assert "localhost" in exc_info.value.detail


def test_runtime_blocks_openai_tts_private_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.OPENAI.value,
            api_key="test-key",
            model="gpt-4o-mini-tts",
            voice="alloy",
            base_url="http://10.0.0.10/v1",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        create_tts_service(user_config, audio_config=None)

    assert exc_info.value.status_code == 400
    assert "public IP" in exc_info.value.detail


def test_runtime_blocks_openai_tts_localhost_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.OPENAI.value,
            api_key="test-key",
            model="gpt-4o-mini-tts",
            voice="alloy",
            base_url="http://localhost:8000/v1",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        create_tts_service(user_config, audio_config=None)

    assert exc_info.value.status_code == 400
    assert "localhost" in exc_info.value.detail


def test_embedding_service_blocks_private_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    with pytest.raises(ValueError, match="public IP"):
        OpenAIEmbeddingService(
            db_client=SimpleNamespace(),
            api_key="test-key",
            base_url="http://10.0.0.10/v1",
        )
