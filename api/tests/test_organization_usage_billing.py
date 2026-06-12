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


@pytest.mark.asyncio
async def test_get_billing_credits_pages_v2_ledger(monkeypatch):
    monkeypatch.setattr(organization_usage, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        organization_usage,
        "_get_mps_billing_account_status",
        AsyncMock(return_value={"billing_mode": "v2"}),
    )
    get_ledger = AsyncMock(
        return_value={
            "account": {
                "id": 7,
                "organization_id": 42,
                "billing_mode": "v2",
                "cached_balance_credits": 250,
                "currency": "USD",
            },
            "ledger_entries": [
                {
                    "id": 99,
                    "entry_type": "grant",
                    "origin": "account_creation",
                    "credits_delta": 250,
                    "balance_after": 250,
                    "created_at": "2026-06-12T00:00:00Z",
                }
            ],
            "total_debits_credits": 75,
            "total_count": 101,
            "page": 3,
            "limit": 25,
            "total_pages": 5,
        }
    )
    monkeypatch.setattr(
        organization_usage.mps_service_key_client,
        "get_credit_ledger",
        get_ledger,
    )

    user = SimpleNamespace(
        provider_id="provider-123",
        selected_organization_id=42,
    )

    response = await organization_usage.get_billing_credits(
        page=3,
        limit=25,
        user=user,
    )

    get_ledger.assert_awaited_once_with(
        organization_id=42,
        page=3,
        limit=25,
        created_by="provider-123",
    )
    assert response.billing_version == "v2"
    assert response.total_credits_used == 75
    assert response.total_count == 101
    assert response.page == 3
    assert response.limit == 25
    assert response.total_pages == 5
    assert response.ledger_entries[0].id == 99
