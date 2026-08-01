"""Capability-token auth for the telephony media WebSocket (issue #598).

The media socket's id triple is otherwise a guessable bearer capability; when a
secret is configured, the URL carries an HMAC ?token= the handler verifies.
These cover the stateless crypto/URL logic — the security-critical surface.
"""
import pytest

from api import constants
from api.services.telephony import ws_auth


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", "unit-test-secret")
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_ENFORCE", False)


@pytest.fixture
def no_secret(monkeypatch):
    monkeypatch.setattr(constants, "TELEPHONY_WS_TOKEN_SECRET", None)


def test_disabled_when_no_secret(no_secret):
    assert ws_auth.token_configured() is False
    assert ws_auth.mint_ws_token(7, 3, 42) is None
    # Byte-for-byte the legacy URL -> adopting the builder is a no-op until opt-in.
    assert (ws_auth.build_media_ws_url("wss://x", 7, 3, 42)
            == "wss://x/api/v1/telephony/ws/7/3/42")
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
    ari_token = ws_auth.mint_ws_token("7", 3, "42")   # ARI: workflow_id/run are str
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
