from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.errors.mps import MPSUnavailableError
from api.routes import organization as organization_routes


@pytest.mark.asyncio
async def test_model_configuration_pricing_returns_empty_in_oss(monkeypatch):
    get_pricing = AsyncMock()
    monkeypatch.setattr(organization_routes, "DEPLOYMENT_MODE", "oss")
    monkeypatch.setattr(
        organization_routes.mps_service_key_client,
        "get_billing_pricing",
        get_pricing,
    )

    response = await organization_routes.get_model_configuration_pricing(
        SimpleNamespace(selected_organization_id=42),
    )

    assert response.platform_usage is None
    assert response.dograh_model is None
    get_pricing.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_configuration_pricing_uses_selected_organization(monkeypatch):
    get_pricing = AsyncMock(
        return_value={
            "organization_id": 42,
            "platform_usage": {
                "metric_code": "platform_usage",
                "display_name": "Platform usage",
                "unit": "minute",
                "price_per_minute": 0.01,
                "currency": "USD",
                "rounding_policy": "ceil_minute",
            },
            "dograh_model": {
                "metric_code": "voice_minutes",
                "display_name": "Dograh model usage",
                "unit": "minute",
                "price_per_minute": 0.07,
                "currency": "USD",
                "rounding_policy": "ceil_minute",
            },
        }
    )
    monkeypatch.setattr(organization_routes, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        organization_routes.mps_service_key_client,
        "get_billing_pricing",
        get_pricing,
    )

    response = await organization_routes.get_model_configuration_pricing(
        SimpleNamespace(selected_organization_id=42),
    )

    get_pricing.assert_awaited_once_with(42)
    assert response.platform_usage is not None
    assert response.platform_usage.price_per_minute == 0.01
    assert response.dograh_model is not None
    assert response.dograh_model.price_per_minute == 0.07


@pytest.mark.asyncio
async def test_model_configuration_pricing_does_not_duplicate_mps_failure(monkeypatch):
    route_log_failure = Mock()
    get_pricing = AsyncMock(
        side_effect=MPSUnavailableError("get_billing_pricing", status_code=503)
    )
    monkeypatch.setattr(organization_routes, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        organization_routes.mps_service_key_client,
        "get_billing_pricing",
        get_pricing,
    )
    monkeypatch.setattr(organization_routes, "log_failure", route_log_failure)

    with pytest.raises(MPSUnavailableError):
        await organization_routes.get_model_configuration_pricing(
            SimpleNamespace(selected_organization_id=42),
        )

    route_log_failure.assert_not_called()
