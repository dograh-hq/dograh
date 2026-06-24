"""Razorpay client: create an order + verify the payment signature.

Signature verification is the security boundary — Razorpay signs
`order_id|payment_id` with HMAC-SHA256 keyed by the secret; we recompute and
compare in constant time before crediting anything. Keys live in env
(RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET); test-mode keys work end-to-end.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional

import httpx
from loguru import logger

from api.constants import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET

RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


def is_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


async def create_order(
    *, amount_paise: int, receipt: str, notes: dict
) -> Optional[dict]:
    """Create a Razorpay order; return the order dict or None on failure."""
    if not is_configured():
        logger.warning("Razorpay not configured (RAZORPAY_KEY_ID/SECRET unset)")
        return None
    auth = base64.b64encode(
        f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()
    ).decode()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                RAZORPAY_ORDERS_URL,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json",
                },
                json={
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": receipt,
                    "notes": notes,
                },
            )
        if not resp.is_success:
            logger.warning(
                f"Razorpay create order failed: {resp.status_code} {resp.text[:200]}"
            )
            return None
        return resp.json()
    except Exception as exc:
        logger.warning(f"Razorpay create order error: {exc}")
        return None


def verify_payment_signature(
    *, order_id: str, payment_id: str, signature: str
) -> bool:
    """True iff `signature` == HMAC_SHA256(order_id|payment_id, key_secret)."""
    if not (RAZORPAY_KEY_SECRET and order_id and payment_id and signature):
        return False
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
