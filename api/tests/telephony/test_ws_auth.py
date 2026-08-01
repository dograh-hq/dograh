"""Capability-token auth for the telephony media WebSocket (issue #598).

The media socket's id triple is otherwise a guessable bearer capability; when a
secret is configured, the URL carries an HMAC ?token= the handler verifies.
These cover the stateless crypto/URL logic — the security-critical surface —
plus the handler gate, every mint site, and the log redaction that keeps the
token (a bearer credential) out of log sinks.
"""

import importlib
import json
import pathlib
from unittest.mock import AsyncMock

import pytest

from api import constants
from api.services.telephony import ws_auth

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", "unit-test-secret")
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_ENFORCE", False)


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", "unit-test-secret")
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_ENFORCE", True)


@pytest.fixture
def no_secret(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", None)


def test_disabled_when_no_secret(no_secret):
    assert ws_auth.token_configured() is False
    assert ws_auth.mint_ws_token(7, 3, 42) is None
    # Byte-for-byte the legacy URL -> adopting the builder is a no-op until opt-in.
    assert (
        ws_auth.build_media_ws_url("wss://x", 7, 3, 42)
        == "wss://x/api/v1/telephony/ws/7/3/42"
    )
    # verify() is always False when disabled; callers gate on token_configured().
    assert ws_auth.verify_ws_token(7, 3, 42, "anything") is False


def test_url_carries_token_when_secret_set(secret):
    assert ws_auth.token_configured() is True
    url = ws_auth.build_media_ws_url("wss://x/", 7, 3, 42)  # trailing slash trimmed
    assert url.startswith("wss://x/api/v1/telephony/ws/7/3/42?token=")


def test_verify_roundtrip_and_tamper(secret):
    tok = ws_auth.mint_ws_token(7, 3, 42)
    assert ws_auth.verify_ws_token(7, 3, 42, tok) is True
    # Any id in the triple changing invalidates the token.
    assert ws_auth.verify_ws_token(7, 3, 43, tok) is False
    assert ws_auth.verify_ws_token(8, 3, 42, tok) is False
    assert ws_auth.verify_ws_token(7, 9, 42, tok) is False
    # Wrong / missing token.
    assert ws_auth.verify_ws_token(7, 3, 42, "deadbeef") is False
    assert ws_auth.verify_ws_token(7, 3, 42, None) is False
    assert ws_auth.verify_ws_token(7, 3, 42, "") is False


def test_non_ascii_token_is_invalid_not_error(secret):
    # `token` is attacker-controlled from the query string; a non-ASCII value
    # must return False, not raise (which would 500 the socket and skip the
    # audit/enforcement path). Regression for cubic P2 on #599.
    tok = ws_auth.mint_ws_token(7, 3, 42)
    assert ws_auth.verify_ws_token(7, 3, 42, "café☕") is False
    assert ws_auth.verify_ws_token(7, 3, 42, "🔑" * 10) is False
    # A genuine token still verifies after the byte-compare change.
    assert ws_auth.verify_ws_token(7, 3, 42, tok) is True


def test_str_and_int_ids_produce_same_token(secret):
    # The ARI path mints with string ids (v() dial params) while the shared
    # handler verifies with int path/query params. f-string rendering makes them
    # identical, so the ARI token must verify under the int triple. Regression
    # for cubic P1 on #599 (ARI enforcement outage).
    ari_token = ws_auth.mint_ws_token("7", 3, "42")  # ARI: workflow_id/run are str
    assert ari_token == ws_auth.mint_ws_token(7, 3, 42)
    assert ws_auth.verify_ws_token(7, 3, 42, ari_token) is True


def test_token_bound_to_secret(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", "secret-a")
    a = ws_auth.mint_ws_token(7, 3, 42)
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", "secret-b")
    b = ws_auth.mint_ws_token(7, 3, 42)
    assert a != b
    # A token minted under secret-a must not verify once the secret rotates.
    assert ws_auth.verify_ws_token(7, 3, 42, a) is False


def test_enforcement_flag(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_ENFORCE", True)
    assert ws_auth.enforcement_enabled() is True
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_ENFORCE", False)
    assert ws_auth.enforcement_enabled() is False


# --------------------------------------------------------------------------
# The handler gate — the control itself, not just the crypto under it.
# --------------------------------------------------------------------------


class _FakeWebSocket:
    """Stand-in for the already-accepted WebSocket the shared handler receives."""

    def __init__(self, query_params: dict | None = None):
        self.query_params = query_params or {}
        self.closed_with: tuple[int, str] | None = None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


@pytest.fixture
def handler(monkeypatch):
    """The shared handler with its first DB call stubbed out.

    Returns (callable, get_workflow_run mock). The mock resolving to None makes
    the handler close 4404 — which is how these tests tell "token accepted, moved
    on to the lookup" apart from "token rejected".
    """
    from api.routes import telephony as telephony_routes

    get_run = AsyncMock(return_value=None)
    monkeypatch.setattr(telephony_routes.db_client, "get_workflow_run", get_run)
    return telephony_routes._handle_telephony_websocket, get_run


async def test_handler_rejects_missing_token_under_enforcement(enforced, handler):
    handle, get_run = handler
    ws = _FakeWebSocket()

    await handle(ws, 7, 3, 42)

    assert ws.closed_with == (4401, "Unauthorized")
    # Rejected before any DB work: a peer that guessed the id triple must not be
    # able to make the handler go look those ids up.
    get_run.assert_not_awaited()


async def test_handler_rejects_forged_token_under_enforcement(enforced, handler):
    handle, get_run = handler
    # Token minted for a different run — the attacker holds a valid token, but
    # not one bound to this triple.
    other = ws_auth.mint_ws_token(7, 3, 99)

    ws = _FakeWebSocket({"token": other})
    await handle(ws, 7, 3, 42)

    assert ws.closed_with == (4401, "Unauthorized")
    get_run.assert_not_awaited()


async def test_handler_accepts_valid_token_under_enforcement(enforced, handler):
    handle, get_run = handler
    ws = _FakeWebSocket({"token": ws_auth.mint_ws_token(7, 3, 42)})

    await handle(ws, 7, 3, 42)

    # Past the gate: it reached the run lookup and closed on that instead.
    assert ws.closed_with == (4404, "Workflow run not found")
    get_run.assert_awaited_once()


async def test_handler_allows_missing_token_when_enforcement_off(secret, handler):
    # Phase 1 of the rollout: tokens are minted and verified, but an unverifiable
    # connection is logged and still served. Nothing may break here.
    handle, get_run = handler
    ws = _FakeWebSocket()

    await handle(ws, 7, 3, 42)

    assert ws.closed_with == (4404, "Workflow run not found")
    get_run.assert_awaited_once()


async def test_handler_skips_check_entirely_without_secret(no_secret, handler):
    handle, get_run = handler
    ws = _FakeWebSocket()

    await handle(ws, 7, 3, 42)

    assert ws.closed_with == (4404, "Workflow run not found")
    get_run.assert_awaited_once()


# --------------------------------------------------------------------------
# Mint sites — every URL a carrier or Asterisk dials back must carry the token.
# --------------------------------------------------------------------------

_WEBHOOK_PROVIDERS = [
    ("twilio", "TwilioProvider"),
    ("plivo", "PlivoProvider"),
    ("vobiz", "VobizProvider"),
    ("vonage", "VonageProvider"),
]


@pytest.mark.parametrize("package, cls_name", _WEBHOOK_PROVIDERS)
async def test_webhook_response_carries_token(secret, monkeypatch, package, cls_name):
    module = importlib.import_module(
        f"api.services.telephony.providers.{package}.provider"
    )
    monkeypatch.setattr(
        module,
        "get_backend_endpoints",
        AsyncMock(return_value=("https://api.test", "wss://api.test")),
    )

    body = await getattr(module, cls_name)({}).get_webhook_response(7, 3, 42)

    assert f"token={ws_auth.mint_ws_token(7, 3, 42)}" in body


async def test_ari_transport_data_carries_token(secret):
    """ARI is the one mint site that isn't a URL — it passes v() dial params.

    Asterisk turns those into the query string on /ws/ari, so a refactor that
    drops the token here fails only under enforcement, in production, on the
    ARI path alone. Pin it.
    """
    from api.services.telephony.ari_manager import ARIConnection

    conn = ARIConnection(
        organization_id=3,
        telephony_configuration_id=1,
        ari_endpoint="http://asterisk:8088",
        app_name="dograh",
        app_password="pw",
        ws_client_name="dograh-ws",
    )
    captured = {}

    async def fake_request(method, path, **kwargs):
        captured["params"] = kwargs["params"]
        return {"id": kwargs["params"].get("channelId", "")}

    conn._ari_request = fake_request
    conn._mark_ext_channel = AsyncMock()

    # Outbound ARI carries the ids as strings parsed out of Stasis app args.
    await conn._create_external_media("7", "42", channel_id="ext-1")

    transport_data = captured["params"]["transport_data"]
    assert transport_data.startswith("v(") and transport_data.endswith(")")

    # Parse it back the way Asterisk hands it to /ws/ari, then run the handler's
    # own verification over the result.
    qp = dict(pair.split("=", 1) for pair in transport_data[2:-1].split(","))
    assert ws_auth.verify_ws_token(
        int(qp["workflow_id"]),
        int(qp["organization_id"]),
        int(qp["workflow_run_id"]),
        qp.get("token"),
    )


def test_no_provider_builds_the_media_url_by_hand():
    """ws_auth owns the media-WS URL; re-inlining it silently skips the token.

    Cheaper than a mint-site test per provider, and it also covers the two
    providers whose mint site sits behind an outbound HTTP call.
    """
    scanned = [
        *(REPO_ROOT / "api/services/telephony/providers").rglob("*.py"),
        REPO_ROOT / "api/services/telephony/ari_manager.py",
        REPO_ROOT / "api/routes/telephony.py",
    ]
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in scanned
        if "/api/v1/telephony/ws" in p.read_text()
    ]
    assert offenders == [], (
        f"{offenders} build the media WebSocket URL directly; "
        "use ws_auth.build_media_ws_url so the capability token is minted"
    )


# --------------------------------------------------------------------------
# Redaction — the token is a bearer credential with no expiry, so a log sink
# that captures it is as good as the socket itself.
# --------------------------------------------------------------------------


def test_redact_token_in_markup_and_json(secret):
    tok = ws_auth.mint_ws_token(7, 3, 42)
    url = ws_auth.build_media_ws_url("wss://api.test", 7, 3, 42)

    # TwiML/CXML attribute, Plivo/Vobiz element text, and a JSON dial payload.
    for document in (
        f'<Stream url="{url}"></Stream>',
        f'<Stream bidirectional="true">{url}</Stream>',
        json.dumps({"stream_url": url, "to": "15551230001"}),
        f"Telnyx dial payload: {url}",
    ):
        redacted = ws_auth.redact_token(document)
        assert tok not in redacted
        assert "token=[REDACTED]" in redacted
        # Everything around the token survives — these lines are still useful.
        assert "wss://api.test/api/v1/telephony/ws/7/3/42" in redacted


def test_redact_token_is_a_noop_without_a_token(no_secret):
    url = ws_auth.build_media_ws_url("wss://api.test", 7, 3, 42)
    assert ws_auth.redact_token(f'<Stream url="{url}"/>') == f'<Stream url="{url}"/>'
