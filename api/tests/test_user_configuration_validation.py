from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.errors.mps import MPSUnavailableError
from api.routes import user as user_routes
from api.schemas.ai_model_configuration import (
    DograhManagedAIModelConfiguration,
    EffectiveAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration import check_validity
from api.services.configuration.ai_model_configuration import (
    ResolvedAIModelConfiguration,
)
from api.services.configuration.check_validity import UserConfigurationValidator


@pytest.mark.asyncio
async def test_validate_user_configurations_marks_stale_org_v2_config_validated(
    monkeypatch,
):
    stale_config = EffectiveAIModelConfiguration(
        last_validated_at=datetime.now(UTC) - timedelta(seconds=120)
    )
    resolved = ResolvedAIModelConfiguration(
        effective=stale_config,
        source="organization_v2",
    )
    validate = AsyncMock(return_value={"status": [{"model": "all", "message": "ok"}]})
    touch_validation_cache = AsyncMock()

    class FakeValidator:
        def __init__(self):
            self.validate = validate

    monkeypatch.setattr(
        user_routes,
        "get_resolved_ai_model_configuration",
        AsyncMock(return_value=resolved),
    )
    monkeypatch.setattr(user_routes, "UserConfigurationValidator", FakeValidator)
    monkeypatch.setattr(
        user_routes,
        "update_organization_ai_model_configuration_last_validated_at",
        touch_validation_cache,
    )

    response = await user_routes.validate_user_configurations(
        validity_ttl_seconds=60,
        user=SimpleNamespace(
            provider_id="provider-123",
            selected_organization_id=42,
        ),
    )

    assert response == {"status": [{"model": "all", "message": "ok"}]}
    validate.assert_awaited_once_with(
        stale_config,
        organization_id=42,
        created_by="provider-123",
    )
    touch_validation_cache.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_validate_user_configurations_uses_fresh_org_v2_validation_cache(
    monkeypatch,
):
    fresh_config = EffectiveAIModelConfiguration(last_validated_at=datetime.now(UTC))
    resolved = ResolvedAIModelConfiguration(
        effective=fresh_config,
        source="organization_v2",
    )
    validate = AsyncMock()
    touch_validation_cache = AsyncMock()

    class FakeValidator:
        def __init__(self):
            self.validate = validate

    monkeypatch.setattr(
        user_routes,
        "get_resolved_ai_model_configuration",
        AsyncMock(return_value=resolved),
    )
    monkeypatch.setattr(user_routes, "UserConfigurationValidator", FakeValidator)
    monkeypatch.setattr(
        user_routes,
        "update_organization_ai_model_configuration_last_validated_at",
        touch_validation_cache,
    )

    response = await user_routes.validate_user_configurations(
        validity_ttl_seconds=60,
        user=SimpleNamespace(
            provider_id="provider-123",
            selected_organization_id=42,
        ),
    )

    assert response == {"status": []}
    validate.assert_not_awaited()
    touch_validation_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_service_key_is_checked_once_per_validation_request(monkeypatch):
    validate_service_key = Mock(return_value=True)
    monkeypatch.setattr(
        check_validity.mps_service_key_client,
        "validate_service_key",
        validate_service_key,
    )
    configuration = compile_ai_model_configuration_v2(
        OrganizationAIModelConfigurationV2(
            mode="dograh",
            dograh=DograhManagedAIModelConfiguration(api_key="mps-shared-key"),
        )
    )
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
    configuration = compile_ai_model_configuration_v2(
        OrganizationAIModelConfigurationV2(
            mode="dograh",
            dograh=DograhManagedAIModelConfiguration(api_key="mps-shared-key"),
        )
    )

    with pytest.raises(MPSUnavailableError):
        await UserConfigurationValidator().validate(
            configuration,
            organization_id=42,
            created_by="provider-123",
        )
