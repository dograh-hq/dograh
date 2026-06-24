"""Razorpay payment-signature verification — the credit security boundary."""

import hashlib
import hmac
from unittest.mock import patch

from api.services.billing import razorpay_client

SECRET = "rzp_test_secret_abc123"


def _sign(order, payment, secret=SECRET):
    return hmac.new(
        secret.encode(), f"{order}|{payment}".encode(), hashlib.sha256
    ).hexdigest()


def _with_secret(secret=SECRET):
    return patch.object(razorpay_client, "RAZORPAY_KEY_SECRET", secret)


def test_valid_signature_passes():
    with _with_secret():
        sig = _sign("order_1", "pay_1")
        assert razorpay_client.verify_payment_signature(
            order_id="order_1", payment_id="pay_1", signature=sig
        )


def test_wrong_signature_fails():
    with _with_secret():
        assert not razorpay_client.verify_payment_signature(
            order_id="order_1", payment_id="pay_1", signature="deadbeef"
        )


def test_tampered_order_id_fails():
    # Attacker keeps a valid signature but swaps the order id -> must fail.
    with _with_secret():
        sig = _sign("order_1", "pay_1")
        assert not razorpay_client.verify_payment_signature(
            order_id="order_HACKED", payment_id="pay_1", signature=sig
        )


def test_signature_from_other_secret_fails():
    with _with_secret():
        sig = _sign("order_1", "pay_1", secret="some_other_secret")
        assert not razorpay_client.verify_payment_signature(
            order_id="order_1", payment_id="pay_1", signature=sig
        )


def test_missing_inputs_fail():
    with _with_secret():
        assert not razorpay_client.verify_payment_signature(
            order_id="", payment_id="pay_1", signature="x"
        )


def test_no_secret_configured_fails_closed():
    with _with_secret(""):
        sig = _sign("order_1", "pay_1")
        assert not razorpay_client.verify_payment_signature(
            order_id="order_1", payment_id="pay_1", signature=sig
        )
        assert razorpay_client.is_configured() is False
