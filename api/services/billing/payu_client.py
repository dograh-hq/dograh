"""PayU Hosted Checkout client: build the signed payment request + verify the
callback response.

The SHA-512 hash keyed by the merchant SALT is the security boundary.

Request hash (sent to PayU):
    sha512(key|txnid|amount|productinfo|firstname|email|udf1|udf2|udf3|udf4|udf5||||||SALT)

Response hash (to verify PayU's surl/furl callback, reverse order):
    sha512([additionalCharges|]SALT|status||||||udf5|udf4|udf3|udf2|udf1|email|firstname|productinfo|amount|txnid|key)

Both are lowercase hex. Keys live in env (PAYU_MERCHANT_KEY / PAYU_MERCHANT_SALT);
the SALT is NEVER sent to the browser. PAYU_MODE=live -> secure.payu.in, else the
test.payu.in sandbox.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Dict

from api.constants import PAYU_MERCHANT_KEY, PAYU_MERCHANT_SALT, PAYU_MODE

PAYU_TEST_PAYMENT_URL = "https://test.payu.in/_payment"
PAYU_LIVE_PAYMENT_URL = "https://secure.payu.in/_payment"


def is_configured() -> bool:
    return bool(PAYU_MERCHANT_KEY and PAYU_MERCHANT_SALT)


def payment_url() -> str:
    """The PayU _payment endpoint for the active mode (live vs test sandbox)."""
    return PAYU_LIVE_PAYMENT_URL if PAYU_MODE == "live" else PAYU_TEST_PAYMENT_URL


def _sha512(value: str) -> str:
    return hashlib.sha512(value.encode("utf-8")).hexdigest()


def request_hash_string(
    *,
    txnid: str,
    amount: str,
    productinfo: str,
    firstname: str,
    email: str,
    udf1: str = "",
    udf2: str = "",
    udf3: str = "",
    udf4: str = "",
    udf5: str = "",
) -> str:
    """The exact pipe-delimited string PayU hashes for the payment request."""
    return (
        f"{PAYU_MERCHANT_KEY}|{txnid}|{amount}|{productinfo}|{firstname}|{email}"
        f"|{udf1}|{udf2}|{udf3}|{udf4}|{udf5}||||||{PAYU_MERCHANT_SALT}"
    )


def build_payment_params(
    *,
    txnid: str,
    amount: str,
    productinfo: str,
    firstname: str,
    email: str,
    phone: str,
    surl: str,
    furl: str,
    udf1: str = "",
    udf2: str = "",
    udf3: str = "",
    udf4: str = "",
    udf5: str = "",
) -> Dict[str, str]:
    """The full set of form fields to POST to PayU (including the request hash)."""
    params = {
        "key": PAYU_MERCHANT_KEY,
        "txnid": txnid,
        "amount": amount,
        "productinfo": productinfo,
        "firstname": firstname,
        "email": email,
        "phone": phone,
        "surl": surl,
        "furl": furl,
        "udf1": udf1,
        "udf2": udf2,
        "udf3": udf3,
        "udf4": udf4,
        "udf5": udf5,
    }
    params["hash"] = _sha512(
        request_hash_string(
            txnid=txnid,
            amount=amount,
            productinfo=productinfo,
            firstname=firstname,
            email=email,
            udf1=udf1,
            udf2=udf2,
            udf3=udf3,
            udf4=udf4,
            udf5=udf5,
        )
    )
    return params


def response_hash_string(params: Dict[str, str]) -> str:
    """The exact reverse-order string PayU hashes in its callback response."""

    def g(key: str) -> str:
        return params.get(key) or ""

    base = (
        f"{PAYU_MERCHANT_SALT}|{g('status')}||||||{g('udf5')}|{g('udf4')}|{g('udf3')}"
        f"|{g('udf2')}|{g('udf1')}|{g('email')}|{g('firstname')}|{g('productinfo')}"
        f"|{g('amount')}|{g('txnid')}|{PAYU_MERCHANT_KEY}"
    )
    additional = params.get("additionalCharges")
    if additional:
        base = f"{additional}|{base}"
    return base


def verify_response_hash(params: Dict[str, str]) -> bool:
    """True iff PayU's callback `hash` matches our recomputed reverse hash.

    This is the trust boundary: only PayU knows the SALT, so a forged callback
    cannot produce a valid hash. Constant-time compare.
    """
    received = (params.get("hash") or "").lower()
    if not (PAYU_MERCHANT_SALT and received):
        return False
    expected = _sha512(response_hash_string(params))
    return hmac.compare_digest(expected, received)
