"""Dial signature verification.

Dial signs both of the things it sends us with the same header shape,
``X-Dial-Signature: t=<unix_seconds>,v1=<hex_hmac_sha256>``:

* the Self-Hosted audio WebSocket, over ``"<t>.<call_id>"`` with the
  Self-Hosted audio signing secret
  (https://docs.getdial.ai/api-reference/self-hosted-audio-protocol/overview#authentication);
* webhook deliveries, over ``"<t>.<raw_body>"`` with the webhook's secret
  (https://docs.getdial.ai/documentation/platform/webhooks#verify-the-signature).
"""

import hashlib
import hmac
import time
from typing import Optional

SIGNATURE_HEADER = "x-dial-signature"
DEFAULT_TOLERANCE_SECONDS = 300


def _parse_header(header: str) -> tuple[Optional[str], Optional[str]]:
    timestamp: Optional[str] = None
    signature: Optional[str] = None
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signature = value
    return timestamp, signature


def verify_dial_signature(
    secret: Optional[str],
    header: Optional[str],
    signed_data: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: Optional[float] = None,
) -> bool:
    """Whether ``header`` is a fresh, valid Dial signature over ``signed_data``."""
    if not secret or not header:
        return False

    timestamp, signature = _parse_header(header)
    if not timestamp or not signature:
        return False

    # Every header field is attacker-controlled. Anything malformed must read as
    # "not signed", never raise: an exception here would surface as a 500 on
    # the webhook route and a 1011 on the media socket instead of a rejection.
    if not (timestamp.isascii() and timestamp.isdigit()) or len(timestamp) > 12:
        return False
    signed_at = int(timestamp)

    current = time.time() if now is None else now
    if abs(current - signed_at) > tolerance_seconds:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{signed_data}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    # Compare bytes: compare_digest raises TypeError on non-ASCII str input.
    return hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8"))


def sign_for_tests(secret: str, signed_data: str, timestamp: int) -> str:
    """Build a header value the way Dial does. Used by the test suite."""
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{signed_data}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"
