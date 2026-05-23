from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    REGISTRY,
    GoogleVertexLLMConfiguration,
    ServiceProviders,
    ServiceType,
)
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_llm_service_from_provider,
)


class TestGoogleVertexLLMConfiguration:
    def test_defaults(self):
        config = GoogleVertexLLMConfiguration(project_id="demo-project")
        assert config.provider == ServiceProviders.GOOGLE_VERTEX
        assert config.model == "gemini-2.5-flash"
        assert config.location == "global"
        assert config.credentials is None
        assert config.api_key is None

    def test_registered_in_llm_registry(self):
        assert ServiceProviders.GOOGLE_VERTEX in REGISTRY[ServiceType.LLM]
        assert (
            REGISTRY[ServiceType.LLM][ServiceProviders.GOOGLE_VERTEX]
            is GoogleVertexLLMConfiguration
        )


class TestGoogleVertexLLMServiceFactory:
    def test_create_llm_service_from_provider_uses_vertex_service(self):
        with patch(
            "api.services.pipecat.service_factory.GoogleVertexLLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.GOOGLE_VERTEX.value,
                model="gemini-2.5-pro",
                api_key=None,
                project_id="demo-project",
                location="us-central1",
                credentials='{"type":"service_account"}',
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["project_id"] == "demo-project"
        assert kwargs["location"] == "us-central1"
        assert kwargs["credentials"] == '{"type":"service_account"}'
        assert kwargs["settings"].model == "gemini-2.5-pro"
        assert kwargs["settings"].temperature == 0.1

    def test_create_llm_service_extracts_vertex_credentials(self):
        user_config = SimpleNamespace(
            llm=SimpleNamespace(
                provider=ServiceProviders.GOOGLE_VERTEX.value,
                api_key=None,
                model="gemini-2.5-flash",
                project_id="demo-project",
                location="us-east4",
                credentials='{"type":"service_account"}',
            )
        )

        with patch(
            "api.services.pipecat.service_factory.GoogleVertexLLMService"
        ) as mock_service:
            create_llm_service(user_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["project_id"] == "demo-project"
        assert kwargs["location"] == "us-east4"
        assert kwargs["credentials"] == '{"type":"service_account"}'


class TestGoogleVertexLLMValidation:
    def test_validator_accepts_vertex_llm_without_api_key(self):
        validator = UserConfigurationValidator()
        config = GoogleVertexLLMConfiguration(
            project_id="demo-project",
            location="us-east4",
            credentials='{"type":"service_account"}',
        )

        assert validator._validate_service(config, "llm") == []

    def test_validator_requires_project_id(self):
        validator = UserConfigurationValidator()
        config = SimpleNamespace(
            provider=ServiceProviders.GOOGLE_VERTEX.value,
            project_id=None,
            location="us-east4",
            credentials='{"type":"service_account"}',
            api_key=None,
        )

        result = validator._validate_service(config, "llm")

        assert result == [
            {"model": "llm", "message": "project_id is required for Google Vertex"}
        ]
