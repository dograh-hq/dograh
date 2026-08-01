"""Capability-token authentication for the telephony media WebSocket.

The media socket ``/api/v1/telephony/ws/{workflow_id}/{organization_id}/{workflow_run_id}``
is dialed back by the carrier/connector, so its URL is caller-visible and the id
triple alone is a *guessable bearer capability*: anyone who supplies a valid
triple can open the socket and drive the run.

When ``TELEPHONY_WS_TOKEN_SECRET`` is configured, the URL is minted with an HMAC
``?token=`` that the handler verifies. An attacker can then no longer connect by
guessing ids — only by holding the server secret. This is a stateless capability
token (HMAC over the id triple); it deliberately does *not* attempt the one-shot
redemption / state-race hardening the handler's ``TODO(security)`` sketches, which
needs a run-creation schema change and is left to a follow-up.

Backward-compatible by construction: with no secret set, :func:`build_media_ws_url`
returns exactly the legacy URL and :func:`verify_ws_token` is never consulted, so
adopting the builder in a provider is a no-op until an operator opts in.
"""

import hashlib
import hmac
from urllib.parse import urlencode

from api import constants

_WS_PATH = "/api/v1/telephony/ws"


def token_configured() -> bool:
    """True when a secret is set, i.e. the feature is enabled."""
    return bool(constants.TELEPHONY_WS_TOKEN_SECRET)


def enforcement_enabled() -> bool:
    """True when an invalid/missing token should reject the connection."""
    return bool(constants.TELEPHONY_WS_TOKEN_ENFORCE)


def _canonical(workflow_id, organization_id, workflow_run_id) -> str:
    return f"{workflow_id}:{organization_id}:{workflow_run_id}"


def mint_ws_token(workflow_id, organization_id, workflow_run_id):
    """Return the HMAC-SHA256 capability token, or ``None`` when no secret is set."""
    secret = constants.TELEPHONY_WS_TOKEN_SECRET
    if not secret:
        return None
    msg = _canonical(workflow_id, organization_id, workflow_run_id).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_ws_token(workflow_id, organization_id, workflow_run_id, token) -> bool:
    """Constant-time compare of a presented token against the expected one.

    Returns ``False`` when no secret is configured or no token was presented, so
    callers should gate on :func:`token_configured` before treating ``False`` as
    a rejection.
    """
    expected = mint_ws_token(workflow_id, organization_id, workflow_run_id)
    if not expected or not token:
        return False
    try:
        # Compare as bytes: hmac.compare_digest rejects non-ASCII *str* args with
        # TypeError, and ``token`` is attacker-controlled from the query string.
        # Anything that can't be compared is simply an invalid token, not a 500.
        return hmac.compare_digest(expected.encode("utf-8"), token.encode("utf-8"))
    except (TypeError, UnicodeError):
        return False


def build_media_ws_url(wss_base, workflow_id, organization_id, workflow_run_id) -> str:
    """Canonical media-WS URL, with ``?token=`` appended when a secret is set.

    With no secret configured this returns the exact legacy URL, so switching a
    provider over to this helper changes nothing until an operator opts in.
    """
    url = (
        f"{wss_base.rstrip('/')}{_WS_PATH}"
        f"/{workflow_id}/{organization_id}/{workflow_run_id}"
    )
    token = mint_ws_token(workflow_id, organization_id, workflow_run_id)
    if token:
        url = f"{url}?{urlencode({'token': token})}"
    return url
