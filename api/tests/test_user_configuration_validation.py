from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.errors.mps import MPSUnavailableError
from api.routes import user as user_routes
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration import check_validity
from api.services.configuration.ai_model_configuration import (
    ResolvedAIModelConfiguration,
)
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    DograhEmbeddingsConfiguration,
    DograhLLMService,
    DograhSTTService,
    DograhTTSService,
)


def _managed_configuration(api_key="mps-shared-key"):
    return EffectiveAIModelConfiguration(
        llm=DograhLLMService(api_key=api_key),
        stt=DograhSTTService(api_key=api_key),
        tts=DograhTTSService(api_key=api_key),
        embeddings=DograhEmbeddingsConfiguration(api_key=api_key),
    )


@pytest.mark.asyncio
async def test_validate_user_configurations_checks_the_resolved_default(monkeypatch):
    configuration = _managed_configuration()
    validate = AsyncMock(return_value={"status": [{"model": "all", "message": "ok"}]})

    class FakeValidator:
        def __init__(self):
            self.validate = validate

    monkeypatch.setattr(
        user_routes,
        "get_resolved_ai_model_configuration",
        AsyncMock(
            return_value=ResolvedAIModelConfiguration(
                effective=configuration, source="organization_v3"
            )
        ),
    )
    monkeypatch.setattr(user_routes, "UserConfigurationValidator", FakeValidator)

    response = await user_routes.validate_user_configurations(
        user=SimpleNamespace(provider_id="provider-123", selected_organization_id=42),
    )

    assert response == {"status": [{"model": "all", "message": "ok"}]}
    validate.assert_awaited_once_with(
        configuration,
        organization_id=42,
        created_by="provider-123",
    )


@pytest.mark.asyncio
async def test_managed_service_key_is_checked_once_per_validation_request(monkeypatch):
    validate_service_key = Mock(return_value=True)
    monkeypatch.setattr(
        check_validity.mps_service_key_client,
        "validate_service_key",
        validate_service_key,
    )
    configuration = _managed_configuration()
    validator = UserConfigurationValidator()

    assert await validator.validate(
        configuration,
        organization_id=42,
        created_by="provider-123",
    ) == {"status": [{"model": "all", "message": "ok"}]}
    assert await validator.validate(
        configuration,
        organization_id=42,
        created_by="provider-123",
    ) == {"status": [{"model": "all", "message": "ok"}]}

    assert validate_service_key.call_count == 2
    validate_service_key.assert_called_with(
        "mps-shared-key",
        organization_id=42,
        created_by="provider-123",
    )


@pytest.mark.asyncio
async def test_mps_outage_is_not_reported_as_invalid_customer_key(monkeypatch):
    monkeypatch.setattr(
        check_validity.mps_service_key_client,
        "validate_service_key",
        Mock(side_effect=MPSUnavailableError("validate_service_key")),
    )

    with pytest.raises(MPSUnavailableError):
        await UserConfigurationValidator().validate(
            _managed_configuration(),
            organization_id=42,
            created_by="provider-123",
        )
