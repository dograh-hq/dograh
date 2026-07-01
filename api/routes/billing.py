"""Razorpay top-up: buy call-minutes that credit the org's call-seconds balance.

Flow: GET /balance (packs + current balance) -> POST /order (creates a Razorpay
order + a 'created' transaction) -> client opens Razorpay Checkout -> POST /verify
(verifies the signature, then credits the transaction's seconds, idempotently).
The credited amount comes from the SERVER-stored transaction, never the client.
"""

from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel

from api.constants import CREDIT_PACKS, RAZORPAY_KEY_ID, UI_APP_URL
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_superuser, get_user
from api.services.billing import payu_client, razorpay_client
from api.services.plans import features_for_plan, get_org_plan
from api.utils.common import get_backend_endpoints

router = APIRouter(prefix="/billing", tags=["billing"])


class CreateOrderRequest(BaseModel):
    pack_id: str


class VerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def _pack(pack_id: str) -> Optional[dict]:
    return next((p for p in CREDIT_PACKS if p["id"] == pack_id), None)


def _org(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="no_organization_selected")
    return user.selected_organization_id


@router.get("/balance")
async def get_balance(user: UserModel = Depends(get_user)):
    """Current call-seconds balance (None = unlimited) + the credit packs."""
    org = _org(user)
    balance = await db_client.get_free_call_seconds_remaining(org)
    plan = await get_org_plan(org)
    return {
        "balance_seconds": balance,
        "unlimited": balance is None,
        # PayU is the active gateway; keep Razorpay as an OR so an env with only
        # Razorpay keys still surfaces the packs.
        "configured": payu_client.is_configured() or razorpay_client.is_configured(),
        "gateway": "payu" if payu_client.is_configured() else "razorpay",
        "packs": CREDIT_PACKS,
        "plan": plan,
        "features": features_for_plan(plan),
    }


@router.post("/order")
async def create_order(
    body: CreateOrderRequest, user: UserModel = Depends(get_user)
):
    """Create a Razorpay order for a credit pack."""
    org = _org(user)
    if not razorpay_client.is_configured():
        raise HTTPException(status_code=503, detail="payments_not_configured")

    balance = await db_client.get_free_call_seconds_remaining(org)
    if balance is None:
        raise HTTPException(status_code=400, detail="org_has_unlimited_calling")

    pack = _pack(body.pack_id)
    if not pack:
        raise HTTPException(status_code=400, detail="unknown_pack")

    seconds = int(pack["minutes"]) * 60
    amount_paise = int(pack["price_inr"]) * 100

    order = await razorpay_client.create_order(
        amount_paise=amount_paise,
        receipt=f"org{org}-{pack['id']}",
        notes={"organization_id": str(org), "pack_id": pack["id"], "seconds": str(seconds)},
    )
    if not order or not order.get("id"):
        raise HTTPException(status_code=502, detail="order_create_failed")

    await db_client.create_transaction(
        organization_id=org,
        created_by=user.id,
        razorpay_order_id=order["id"],
        pack_id=pack["id"],
        seconds=seconds,
        amount_paise=amount_paise,
    )
    return {
        "order_id": order["id"],
        "amount_paise": amount_paise,
        "currency": "INR",
        "key_id": RAZORPAY_KEY_ID,
        "pack": pack,
    }


@router.post("/verify")
async def verify_payment(body: VerifyRequest, user: UserModel = Depends(get_user)):
    """Verify the Razorpay signature and credit the purchased minutes (idempotent)."""
    org = _org(user)
    txn = await db_client.get_transaction_by_order_id(body.razorpay_order_id, org)
    if not txn:
        raise HTTPException(status_code=404, detail="order_not_found")

    if txn.status == "paid":  # idempotent — already credited
        balance = await db_client.get_free_call_seconds_remaining(org)
        return {"ok": True, "balance_seconds": balance, "already": True}

    if not razorpay_client.verify_payment_signature(
        order_id=body.razorpay_order_id,
        payment_id=body.razorpay_payment_id,
        signature=body.razorpay_signature,
    ):
        logger.warning(f"Razorpay signature mismatch for order {body.razorpay_order_id}")
        raise HTTPException(status_code=400, detail="signature_verification_failed")

    await db_client.mark_transaction_paid(
        body.razorpay_order_id, body.razorpay_payment_id
    )
    balance = await db_client.add_call_seconds(org, txn.seconds)
    logger.info(
        f"Razorpay top-up: org {org} credited {txn.seconds}s "
        f"(order {body.razorpay_order_id}); balance now {balance}"
    )
    return {"ok": True, "balance_seconds": balance}


@router.get("/transactions")
async def list_transactions(user: UserModel = Depends(get_user)):
    org = _org(user)
    txns = await db_client.list_transactions(org)
    return [
        {
            "id": t.id,
            "pack_id": t.pack_id,
            "seconds": t.seconds,
            "amount_paise": t.amount_paise,
            "status": t.status,
            "created_at": t.created_at,
        }
        for t in txns
    ]


# ======== PayU Hosted Checkout ========


def _payu_customer_fields(user: UserModel) -> tuple[str, str, str]:
    """(firstname, email, phone) for the PayU request. We only reliably have the
    email locally, so firstname derives from it and phone falls back to a
    placeholder (PayU requires the field; the user re-enters real details on the
    PayU page if needed)."""
    email = user.email or "customer@auto4you.in"
    firstname = (email.split("@", 1)[0] or "Customer")[:60]
    phone = getattr(user, "phone", None) or "9999999999"
    return firstname, email, phone


@router.post("/payu/initiate")
async def payu_initiate(
    body: CreateOrderRequest, user: UserModel = Depends(get_user)
):
    """Create a PayU Hosted Checkout request for a credit pack.

    Returns the PayU payment URL + the signed form params; the browser auto-POSTs
    them to PayU. The credited amount comes from the SERVER-stored transaction,
    never the client.
    """
    org = _org(user)
    if not payu_client.is_configured():
        raise HTTPException(status_code=503, detail="payments_not_configured")

    balance = await db_client.get_free_call_seconds_remaining(org)
    if balance is None:
        raise HTTPException(status_code=400, detail="org_has_unlimited_calling")

    pack = _pack(body.pack_id)
    if not pack:
        raise HTTPException(status_code=400, detail="unknown_pack")

    seconds = int(pack["minutes"]) * 60
    amount = f'{int(pack["price_inr"])}.00'
    txnid = "a4y" + uuid4().hex[:20]

    # Reuse the existing txn row; the PayU txnid occupies the gateway-order-id slot.
    await db_client.create_transaction(
        organization_id=org,
        created_by=user.id,
        razorpay_order_id=txnid,
        pack_id=pack["id"],
        seconds=seconds,
        amount_paise=int(pack["price_inr"]) * 100,
    )

    firstname, email, phone = _payu_customer_fields(user)
    backend_endpoint, _ = await get_backend_endpoints()
    callback = f"{backend_endpoint}/api/v1/billing/payu/callback"
    params = payu_client.build_payment_params(
        txnid=txnid,
        amount=amount,
        productinfo=pack["label"],
        firstname=firstname,
        email=email,
        phone=phone,
        surl=callback,
        furl=callback,
        udf1=str(org),
        udf2=pack["id"],
    )
    return {"payment_url": payu_client.payment_url(), "params": params}


@router.post("/payu/test-initiate")
async def payu_test_initiate(user: UserModel = Depends(get_superuser)):
    """Superuser-only ₹30 live PayU test to verify the gateway end-to-end.

    Priced tiny and superuser-gated so it's never a customer-facing purchase.
    Not gated on a metered balance — the callback skips crediting unlimited
    (NULL) orgs. pack_id='test' never affects plan tier (only starter/growth/
    scale are ranked).
    """
    org = _org(user)
    if not payu_client.is_configured():
        raise HTTPException(status_code=503, detail="payments_not_configured")

    txnid = "a4ytest" + uuid4().hex[:16]
    await db_client.create_transaction(
        organization_id=org,
        created_by=user.id,
        razorpay_order_id=txnid,
        pack_id="test",
        seconds=60,  # nominal 1-minute credit on success (skipped for NULL orgs)
        amount_paise=3000,
    )

    firstname, email, phone = _payu_customer_fields(user)
    backend_endpoint, _ = await get_backend_endpoints()
    callback = f"{backend_endpoint}/api/v1/billing/payu/callback"
    params = payu_client.build_payment_params(
        txnid=txnid,
        amount="30.00",
        productinfo="PayU Test Rs 30",
        firstname=firstname,
        email=email,
        phone=phone,
        surl=callback,
        furl=callback,
        udf1=str(org),
        udf2="test",
    )
    return {"payment_url": payu_client.payment_url(), "params": params}


async def _apply_payu_payment(params: dict) -> str:
    """Verify a PayU response and credit its transaction idempotently.

    Shared by the browser callback (surl/furl) and the server-to-server webhook.
    Returns "success", "already" (already credited), or "failed".
    """
    if not payu_client.verify_response_hash(params):
        logger.warning(f"PayU hash mismatch (txnid={params.get('txnid')})")
        return "failed"

    txnid = params.get("txnid") or ""
    status = (params.get("status") or "").lower()
    txn = await db_client.get_transaction_by_order_id_unscoped(txnid)
    if not txn:
        return "failed"
    if txn.status == "paid":  # idempotent — already credited
        return "already"

    # The hash already covers amount; confirm it matches the stored txn.
    try:
        amount_ok = abs(float(params.get("amount") or 0) - txn.amount_paise / 100) < 0.01
    except ValueError:
        amount_ok = False

    if status == "success" and amount_ok:
        await db_client.mark_transaction_paid(txnid, params.get("mihpayid") or "payu")
        # Never convert an unlimited (NULL) org to metered — skip crediting it.
        current = await db_client.get_free_call_seconds_remaining(txn.organization_id)
        if current is not None:
            balance = await db_client.add_call_seconds(txn.organization_id, txn.seconds)
        else:
            balance = "unlimited (not credited)"
        logger.info(
            f"PayU payment ok: org {txn.organization_id} +{txn.seconds}s "
            f"(txnid {txnid}); balance now {balance}"
        )
        return "success"

    logger.info(f"PayU payment not successful (txnid={txnid}, status={status})")
    return "failed"


@router.post("/payu/callback")
async def payu_callback(request: Request):
    """PayU surl/furl browser handler — verify + credit idempotently, then redirect."""
    params = {k: str(v) for k, v in (await request.form()).items()}
    outcome = await _apply_payu_payment(params)
    ok = outcome in ("success", "already")
    result_page = f"{UI_APP_URL.rstrip('/')}/credits"
    return RedirectResponse(
        f"{result_page}?payment={'success' if ok else 'failed'}", status_code=303
    )


@router.post("/payu/webhook")
async def payu_webhook(request: Request):
    """PayU server-to-server webhook — same verification, credits idempotently.

    Backstop so a payment is credited even if the browser never returns to surl.
    Configure this URL in the PayU dashboard webhook settings.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        raw = await request.json()
    else:
        raw = await request.form()
    params = {k: str(v) for k, v in dict(raw).items()}
    outcome = await _apply_payu_payment(params)
    return {"ok": outcome in ("success", "already"), "outcome": outcome}
