"""PayU billing routes: initiate builds a signed request + a server-side txn;
callback credits only on a verified, successful, correct-amount response."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes import billing


class _Req:
    def __init__(self, data):
        self._data = data

    async def form(self):
        return self._data


def _user(org=4):
    return SimpleNamespace(id=7, selected_organization_id=org, email="amit@x.test")


@pytest.mark.asyncio
async def test_payu_initiate_creates_txn_and_returns_signed_params(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        billing.payu_client, "payment_url", lambda: "https://test.payu.in/_payment"
    )
    monkeypatch.setattr(
        billing.payu_client,
        "build_payment_params",
        lambda **kw: {"key": "XxWLV8", "txnid": kw["txnid"], "hash": "h", "amount": kw["amount"]},
    )
    with (
        patch.object(billing.db_client, "get_free_call_seconds_remaining", new=AsyncMock(return_value=1800)),
        patch.object(billing.db_client, "create_transaction", new=AsyncMock()) as create,
        patch.object(billing, "get_backend_endpoints", new=AsyncMock(return_value=("https://api.auto4you.in", "m"))),
    ):
        res = await billing.payu_initiate(
            billing.CreateOrderRequest(pack_id="starter"), user=_user()
        )

    assert res["payment_url"] == "https://test.payu.in/_payment"
    assert res["params"]["key"] == "XxWLV8"
    assert res["params"]["amount"] == "2399.00"
    kw = create.await_args.kwargs
    assert kw["pack_id"] == "starter"
    assert kw["amount_paise"] == 239900
    assert kw["seconds"] == 300 * 60
    assert kw["razorpay_order_id"].startswith("a4y")  # PayU txnid in the gateway slot


@pytest.mark.asyncio
async def test_payu_initiate_rejects_unlimited_org(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "is_configured", lambda: True)
    with patch.object(
        billing.db_client, "get_free_call_seconds_remaining", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(billing.HTTPException) as exc:
            await billing.payu_initiate(
                billing.CreateOrderRequest(pack_id="starter"), user=_user()
            )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_payu_callback_credits_on_verified_success(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    txn = SimpleNamespace(status="created", organization_id=4, seconds=18000, amount_paise=239900)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "mark_transaction_paid", new=AsyncMock()) as mark,
        patch.object(billing.db_client, "add_call_seconds", new=AsyncMock(return_value=19800)) as add,
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "2399.00", "mihpayid": "MP1", "hash": "h"})
        )
    assert resp.status_code == 303
    assert "payment=success" in resp.headers["location"]
    mark.assert_awaited_once_with("a4yX", "MP1")
    add.assert_awaited_once_with(4, 18000)


@pytest.mark.asyncio
async def test_payu_callback_rejects_bad_hash(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: False)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    with (
        patch.object(billing.db_client, "mark_transaction_paid", new=AsyncMock()) as mark,
        patch.object(billing.db_client, "add_call_seconds", new=AsyncMock()) as add,
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "2399.00", "hash": "forged"})
        )
    assert resp.status_code == 303
    assert "payment=failed" in resp.headers["location"]
    mark.assert_not_awaited()
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_payu_callback_idempotent_when_already_paid(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    txn = SimpleNamespace(status="paid", organization_id=4, seconds=18000, amount_paise=239900)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "add_call_seconds", new=AsyncMock()) as add,
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "2399.00", "hash": "h"})
        )
    assert "payment=success" in resp.headers["location"]
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_payu_callback_rejects_amount_mismatch(monkeypatch):
    monkeypatch.setattr(billing.payu_client, "verify_response_hash", lambda p: True)
    monkeypatch.setattr(billing, "UI_APP_URL", "https://app.auto4you.in")
    txn = SimpleNamespace(status="created", organization_id=4, seconds=18000, amount_paise=239900)
    with (
        patch.object(billing.db_client, "get_transaction_by_order_id_unscoped", new=AsyncMock(return_value=txn)),
        patch.object(billing.db_client, "add_call_seconds", new=AsyncMock()) as add,
    ):
        resp = await billing.payu_callback(
            _Req({"status": "success", "txnid": "a4yX", "amount": "1.00", "hash": "h"})
        )
    assert "payment=failed" in resp.headers["location"]
    add.assert_not_awaited()
