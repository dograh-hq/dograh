from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes import organization_usage


@pytest.mark.asyncio
async def test_get_billing_credits_oss_aggregates_by_created_by(monkeypatch):
    monkeypatch.setattr(organization_usage, "DEPLOYMENT_MODE", "oss")
    get_usage = AsyncMock(
        return_value={"total_credits_used": 12.5, "remaining_credits": 487.5}
    )
    monkeypatch.setattr(
        organization_usage.mps_service_key_client,
        "get_usage_by_created_by",
        get_usage,
    )

    user = SimpleNamespace(provider_id="provider-123", selected_organization_id=None)

    response = await organization_usage.get_billing_credits(
        page=1,
        limit=50,
        user=user,
    )

    get_usage.assert_awaited_once_with("provider-123")
    assert response.total_credits_used == 12.5
    assert response.remaining_credits == 487.5
    assert response.total_quota == 500.0
    assert response.ledger_entries == []


@pytest.mark.asyncio
async def test_get_billing_credits_pages_hosted_ledger(monkeypatch):
    monkeypatch.setattr(organization_usage, "DEPLOYMENT_MODE", "saas")
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
    assert response.total_credits_used == 75
    assert response.total_count == 101
    assert response.page == 3
    assert response.limit == 25
    assert response.total_pages == 5
    assert response.ledger_entries[0].id == 99
