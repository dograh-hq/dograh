from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
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


@pytest.mark.parametrize("timezone", ["Not/A_Zone", "../UTC"])
def test_unresolvable_billing_timezone_falls_back_to_utc(monkeypatch, timezone):
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
            "ledger_entries": [],
        }
    )
    monkeypatch.setattr(
        organization_usage.mps_service_key_client, "get_credit_ledger", get_ledger
    )
    app = FastAPI()
    app.include_router(organization_usage.router)
    app.dependency_overrides[organization_usage.get_user] = lambda: SimpleNamespace(
        provider_id="provider-123", selected_organization_id=42
    )

    with TestClient(app) as client:
        response = client.get(
            "/organizations/billing/credits",
            params={
                "timezone": timezone,
                "start_date": "2026-09-30",
                "end_date": "2026-09-30",
            },
        )

    assert response.status_code == 200
    assert response.json()["remaining_credits"] == 250
    get_ledger.assert_awaited_once_with(
        organization_id=42,
        page=1,
        limit=50,
        created_by="provider-123",
        entry_type=None,
        start_date=datetime.fromisoformat("2026-09-30T00:00:00+00:00"),
        end_date=datetime.fromisoformat("2026-10-01T00:00:00+00:00"),
    )


def _billing_client(monkeypatch, mode="saas", organization_id=42, **mps_methods):
    monkeypatch.setattr(organization_usage, "DEPLOYMENT_MODE", mode)
    for name, method in mps_methods.items():
        monkeypatch.setattr(organization_usage.mps_service_key_client, name, method)
    app = FastAPI()
    app.include_router(organization_usage.router)
    app.dependency_overrides[organization_usage.get_user] = lambda: SimpleNamespace(
        provider_id="provider-123", selected_organization_id=organization_id
    )
    return TestClient(app)


def test_billing_payments_lists_mps_payments(monkeypatch):
    payment = {
        "payment_order_id": 200,
        "paid_at": "2026-10-26T09:30:00Z",
        "amount_minor": 5000,
        "currency": "USD",
        "gateway_payment_id": "pay_123",
        "invoice_number": "DC-2627-102",
        "invoice_status": "issued",
    }
    list_payments = AsyncMock(return_value={"payments": [payment]})
    with _billing_client(monkeypatch, list_payments=list_payments) as client:
        response = client.get("/organizations/billing/payments")

    assert response.status_code == 200
    assert response.json() == {"payments": [payment]}
    list_payments.assert_awaited_once_with(
        organization_id=42, created_by="provider-123"
    )


def test_invoice_pdf_is_passed_through_from_mps(monkeypatch):
    pdf = httpx.Response(
        200,
        content=b"%PDF-1.4 test",
        headers={"content-disposition": 'attachment; filename="DC-2627-102.pdf"'},
    )
    get_invoice_pdf = AsyncMock(return_value=pdf)
    with _billing_client(monkeypatch, get_invoice_pdf=get_invoice_pdf) as client:
        response = client.get("/organizations/billing/invoices/200/pdf")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="DC-2627-102.pdf"'
    )
    get_invoice_pdf.assert_awaited_once_with(
        organization_id=42, payment_order_id=200, created_by="provider-123"
    )


@pytest.mark.parametrize(("mps_status", "status"), [(404, 404), (500, 502)])
def test_invoice_pdf_maps_mps_errors(monkeypatch, mps_status, status):
    request = httpx.Request("GET", "http://mps/invoices/200/pdf")
    error = httpx.HTTPStatusError(
        "mps", request=request, response=httpx.Response(mps_status, request=request)
    )
    get_invoice_pdf = AsyncMock(side_effect=error)
    with _billing_client(monkeypatch, get_invoice_pdf=get_invoice_pdf) as client:
        response = client.get("/organizations/billing/invoices/200/pdf")

    assert response.status_code == status


@pytest.mark.parametrize(
    "path",
    ["/organizations/billing/payments", "/organizations/billing/invoices/200/pdf"],
)
def test_invoice_routes_are_404_in_oss_even_without_an_organization(monkeypatch, path):
    with _billing_client(monkeypatch, mode="oss", organization_id=None) as client:
        response = client.get(path)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/organizations/billing/payments", "/organizations/billing/invoices/200/pdf"],
)
def test_invoice_routes_keep_the_clients_own_http_errors(monkeypatch, path):
    error = HTTPException(
        status_code=400, detail="Hosted billing requires a Stack team"
    )
    with _billing_client(
        monkeypatch,
        list_payments=AsyncMock(side_effect=error),
        get_invoice_pdf=AsyncMock(side_effect=error),
    ) as client:
        response = client.get(path)

    assert response.status_code == 400
    assert response.json()["detail"] == "Hosted billing requires a Stack team"


def test_billing_payments_reports_a_malformed_mps_payload_as_502(monkeypatch):
    malformed = {"payments": [{"payment_order_id": 200, "paid_at": None}]}
    with _billing_client(
        monkeypatch, list_payments=AsyncMock(return_value=malformed)
    ) as client:
        response = client.get("/organizations/billing/payments")

    assert response.status_code == 502
