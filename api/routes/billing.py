"""Razorpay top-up: buy call-minutes that credit the org's call-seconds balance.

Flow: GET /balance (packs + current balance) -> POST /order (creates a Razorpay
order + a 'created' transaction) -> client opens Razorpay Checkout -> POST /verify
(verifies the signature, then credits the transaction's seconds, idempotently).
The credited amount comes from the SERVER-stored transaction, never the client.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.constants import CREDIT_PACKS, RAZORPAY_KEY_ID
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.billing import razorpay_client

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
    return {
        "balance_seconds": balance,
        "unlimited": balance is None,
        "configured": razorpay_client.is_configured(),
        "packs": CREDIT_PACKS,
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
