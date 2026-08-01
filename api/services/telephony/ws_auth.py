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
import re
from urllib.parse import urlencode

from loguru import logger

from api import constants

_WS_PATH = "/api/v1/telephony/ws"

# Ids reach the mint from two shapes: ints from the DB (carrier providers,
# inbound routes) and decimal strings parsed out of Asterisk Stasis app args
# (the ARI path). Both render identically through the f-string in _canonical.
Id = int | str


def token_configured() -> bool:
    """True when a secret is set, i.e. the feature is enabled."""
    return bool(constants.TELEPHONY_WS_TOKEN_SECRET)


def enforcement_enabled() -> bool:
    """True when an invalid/missing token should reject the connection."""
    return bool(constants.TELEPHONY_WS_TOKEN_ENFORCE)


def _canonical(workflow_id: Id, organization_id: Id, workflow_run_id: Id) -> str:
    return f"{workflow_id}:{organization_id}:{workflow_run_id}"


def mint_ws_token(
    workflow_id: Id, organization_id: Id, workflow_run_id: Id
) -> str | None:
    """Return the HMAC-SHA256 capability token, or ``None`` when no secret is set."""
    secret = constants.TELEPHONY_WS_TOKEN_SECRET
    if not secret:
        return None
    msg = _canonical(workflow_id, organization_id, workflow_run_id).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_ws_token(
    workflow_id: Id, organization_id: Id, workflow_run_id: Id, token: str | None
) -> bool:
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


def build_media_ws_url(
    wss_base: str, workflow_id: Id, organization_id: Id, workflow_run_id: Id
) -> str:
    """Canonical media-WS URL, with ``?token=`` appended when a secret is set.

    With no secret configured this returns the exact legacy URL, so switching a
    provider over to this helper changes nothing until an operator opts in.

    The returned URL carries a bearer capability — pass it through
    :func:`redact_token` before logging it or the document it is embedded in.
    """
    url = (
        f"{wss_base.rstrip('/')}{_WS_PATH}"
        f"/{workflow_id}/{organization_id}/{workflow_run_id}"
    )
    token = mint_ws_token(workflow_id, organization_id, workflow_run_id)
    if token:
        url = f"{url}?{urlencode({'token': token})}"
    return url


# Stops at whatever delimits the token in the surrounding document: whitespace,
# a quote (TwiML/CXML attribute), & (another query param) or < (element text).
_TOKEN_IN_TEXT = re.compile(r"(token=)[^\s\"'&<>]+")


def redact_token(text: str) -> str:
    """Mask any ``token=…`` in *text* so it is safe to log.

    The token is a bearer capability that neither expires nor is redeemed once,
    so anything that reaches a log sink — the rendered TwiML/CXML/NCCO, a bare
    URL — must be redacted first, or log access becomes socket access.
    """
    return _TOKEN_IN_TEXT.sub(r"\1[REDACTED]", text)


def log_configuration_status() -> None:
    """Report the rollout state once at process start.

    Enforcement is gated on a secret being present (the handler only checks a
    token when :func:`token_configured`), so setting only the enforce flag is
    silently a no-op — neither minting nor rejecting. Say so loudly instead.

    Called from the ARI manager's entrypoint: it mints while the API verifies,
    so a secret present on one side but not the other is a configuration error
    that would otherwise only surface as dropped calls.
    """
    if not token_configured():
        if enforcement_enabled():
            logger.error(
                "TELEPHONY_WS_TOKEN_ENFORCE is set but TELEPHONY_WS_TOKEN_SECRET is "
                "not — telephony media WebSocket tokens are neither minted nor "
                "enforced. Set the secret on every process that mints or verifies "
                "(api, ari-manager) to turn the feature on."
            )
        return

    if enforcement_enabled():
        logger.info(
            "Telephony media WebSocket: capability tokens enforced — connections "
            "without a valid token are rejected with 4401."
        )
    else:
        logger.info(
            "Telephony media WebSocket: capability tokens minted and verified, "
            "enforcement off — invalid tokens are logged and still allowed. Set "
            "TELEPHONY_WS_TOKEN_ENFORCE=true once the logs stay clean."
        )
