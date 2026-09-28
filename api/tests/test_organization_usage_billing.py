from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
@pytest.mark.parametrize(
    ("day", "timezone", "start_at", "end_at"),
    [
        (None, "UTC", None, None),
        (
            date(2026, 3, 8),
            "America/Los_Angeles",
            "2026-03-08T08:00:00+00:00",
            "2026-03-09T07:00:00+00:00",
        ),
        (
            date(2026, 11, 1),
            "America/Los_Angeles",
            "2026-11-01T07:00:00+00:00",
            "2026-11-02T08:00:00+00:00",
        ),
        (
            date(2026, 6, 12),
            "Asia/Kolkata",
            "2026-06-11T18:30:00+00:00",
            "2026-06-12T18:30:00+00:00",
        ),
    ],
)
async def test_get_billing_credits_pages_hosted_ledger(
    monkeypatch, day, timezone, start_at, end_at
):
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
        entry_type="credit",
        start_date=day,
        end_date=day,
        timezone=timezone,
    )

    get_ledger.assert_awaited_once_with(
        organization_id=42,
        page=3,
        limit=25,
        created_by="provider-123",
        entry_type="credit",
        start_date=datetime.fromisoformat(start_at) if start_at else None,
        end_date=datetime.fromisoformat(end_at) if end_at else None,
    )
    assert response.total_credits_used == 75
    assert response.total_count == 101
    assert response.page == 3
    assert response.limit == 25
    assert response.total_pages == 5
    assert response.ledger_entries[0].id == 99


@pytest.mark.parametrize(
    "query",
    [
        {"entry_type": "invalid"},
        {"start_date": "not-a-date"},
        {"start_date": "2026-07-01", "end_date": "2026-06-01"},
        {"timezone": "Not/A_Zone"},
        {"end_date": "9999-12-31"},
    ],
)
def test_invalid_billing_filters_return_422(monkeypatch, query):
    monkeypatch.setattr(organization_usage, "DEPLOYMENT_MODE", "saas")
    get_ledger = AsyncMock()
    monkeypatch.setattr(
        organization_usage.mps_service_key_client, "get_credit_ledger", get_ledger
    )
    app = FastAPI()
    app.include_router(organization_usage.router)
    app.dependency_overrides[organization_usage.get_user] = lambda: SimpleNamespace(
        provider_id="provider-123",
        selected_organization_id=42,
    )
    with TestClient(app) as client:
        assert (
            client.get("/organizations/billing/credits", params=query).status_code
            == 422
        )
    get_ledger.assert_not_awaited()
