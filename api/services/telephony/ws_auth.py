"""Capability-token authentication for the telephony media WebSocket.

The media socket ``/api/v1/telephony/ws/{workflow_id}/{organization_id}/{workflow_run_id}``
is dialed back by the carrier/connector, so its URL is caller-visible and the id
triple alone is a *guessable bearer capability*: anyone who supplies a valid
triple can open the socket and drive the run.

When ``TELEPHONY_WS_TOKEN_SECRET`` is configured, the URL is minted with an HMAC
token — a trailing path segment for carriers, ``?token=`` for ARI (see
:func:`build_media_ws_url`) — that the handler verifies. An attacker can then no
longer connect by guessing ids — only by holding the server secret, or by
reading a log line: the token appears in full in uvicorn's and nginx's access
logs. This is a stateless capability token (HMAC over the id triple); it
deliberately does *not* attempt the one-shot redemption / state-race hardening
the handler's ``TODO(security)`` sketches, which needs a run-creation schema
change and is left to a follow-up.

Backward-compatible by construction: with no secret set, :func:`build_media_ws_url`
returns exactly the legacy URL and :func:`verify_ws_token` is never consulted, so
adopting the builder in a provider is a no-op until an operator opts in.
"""

import hashlib
import hmac
import re

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
    """Canonical media-WS URL, with the token as a trailing path segment.

    The token rides in the *path* rather than a query string because carriers do
    not reliably forward one. Twilio documents the restriction outright — "The
    ``url`` does not support query string parameters" — and drops everything
    after ``?`` when it dials back, so a ``?token=`` URL arrived unauthenticated
    and was rejected 4401 under enforcement. None of the other carriers promise
    query strings survive either; each documents its own channel for custom
    values instead (Plivo ``extraHeaders``, Twilio/Telnyx ``<Parameter>``,
    Vonage ``headers``). The path is the one transport common to all of them,
    and it keeps verification at connect time — the ``<Parameter>`` route would
    only deliver the token in the ``start`` frame, after the socket is accepted.

    Asterisk/ARI is the exception and still uses ``?token=``: it is our own
    client, so nothing strips it, and its URL is assembled from
    ``websocket_client.conf`` plus ``v()`` dial params rather than here — see
    ``ari_manager._create_external_media``. Both transports meet again in
    ``_handle_telephony_websocket``, which verifies whichever one arrives.

    With no secret configured this returns the exact legacy URL, so switching a
    provider over to this helper changes nothing until an operator opts in.

    The returned URL carries a bearer capability. :func:`redact_token` only
    masks the ``?token=`` form, so the path form does reach the logs — see the
    note there.
    """
    url = (
        f"{wss_base.rstrip('/')}{_WS_PATH}"
        f"/{workflow_id}/{organization_id}/{workflow_run_id}"
    )
    token = mint_ws_token(workflow_id, organization_id, workflow_run_id)
    if token:
        # An HMAC hexdigest by construction, so it needs no percent-encoding.
        url = f"{url}/{token}"
    return url


# Stops at whatever delimits the token in the surrounding document: whitespace,
# a quote (TwiML/CXML attribute), & (another query param) or < (element text).
_TOKEN_IN_TEXT = re.compile(r"(token=)[^\s\"'&<>]+")


def redact_token(text: str) -> str:
    """Mask any ``token=…`` in *text* so it is safe to log.

    Only the query form. Since the carrier token moved into the URL path it is
    no longer masked anywhere — deliberately: uvicorn and nginx both log the
    request path in full, so masking our own lines bought little for the
    machinery it took. Treat log access to this deployment as socket access.
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
