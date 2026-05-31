from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import SpeachesLLMConfiguration
from api.utils.url_security import validate_user_configured_service_url


def test_oss_allows_local_service_urls():
    validate_user_configured_service_url(
        "http://localhost:11434/v1",
        field_name="base_url",
    )


def test_validator_allows_speaches_local_base_url_in_oss():
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


def test_embedding_service_blocks_private_base_url_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")

    with pytest.raises(ValueError, match="public IP"):
        OpenAIEmbeddingService(
            db_client=SimpleNamespace(),
            api_key="test-key",
            base_url="http://10.0.0.10/v1",
        )
