from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes import organization_usage


def test_is_mps_billing_v2_depends_only_on_account_mode():
    assert organization_usage._is_mps_billing_v2({"billing_mode": "v2"}) is True
    assert organization_usage._is_mps_billing_v2({"billing_mode": "v1"}) is False
    assert organization_usage._is_mps_billing_v2({"billing_mode": "shadow"}) is False
    assert organization_usage._is_mps_billing_v2(None) is False


@pytest.mark.asyncio
async def test_get_mps_billing_account_status_uses_user_provider_id(monkeypatch):
    get_status = AsyncMock(return_value={"billing_mode": "v2"})
    monkeypatch.setattr(
        organization_usage.mps_service_key_client,
        "get_billing_account_status",
        get_status,
    )

    user = SimpleNamespace(provider_id="provider-123")

    assert await organization_usage._get_mps_billing_account_status(user, 42) == {
        "billing_mode": "v2"
    }
    get_status.assert_awaited_once_with(
        organization_id=42,
        created_by="provider-123",
    )
