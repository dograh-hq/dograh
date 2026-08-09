import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.errors.mps import MPS_UNAVAILABLE_PUBLIC_MESSAGE, MPSUnavailableError
from api.routes import user as user_routes


@pytest.mark.asyncio
async def test_voice_catalog_does_not_duplicate_mps_failure(monkeypatch):
    route_log_failure = Mock()
    get_voices = AsyncMock(side_effect=MPSUnavailableError("get_voices"))
    monkeypatch.setattr(
        user_routes.mps_service_key_client,
        "get_voices",
        get_voices,
    )
    monkeypatch.setattr(user_routes, "log_failure", route_log_failure)

    with pytest.raises(MPSUnavailableError):
        await user_routes.get_voices(
            provider="cartesia",
            user=SimpleNamespace(
                selected_organization_id=42,
                provider_id="provider-123",
            ),
        )

    route_log_failure.assert_not_called()


@pytest.mark.asyncio
async def test_mps_unavailable_handler_returns_customer_safe_503():
    from api.app import app, handle_mps_unavailable_error

    assert app.exception_handlers[MPSUnavailableError] is handle_mps_unavailable_error

    response = await handle_mps_unavailable_error(
        None,
        MPSUnavailableError("validate_service_key", status_code=503),
    )

    assert response.status_code == 503
    assert json.loads(response.body) == {"detail": MPS_UNAVAILABLE_PUBLIC_MESSAGE}
