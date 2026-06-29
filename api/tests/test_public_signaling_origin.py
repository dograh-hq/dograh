"""Tests for public WebRTC signaling allowed-domain enforcement.

Regression for issue #330: the public signaling WebSocket
(`/public/signaling/{session_token}`) must enforce the embed token's
allowed-domain policy, mirroring the HTTP embed endpoints. Before the fix it
validated only the session token and expiry, so a leaked or replayed session
token could attach to the signaling path from an arbitrary origin.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class _FakeWebSocket:
    """Minimal WebSocket double exposing handshake headers and close()."""

    def __init__(self, origin: str):
        self.headers = {"origin": origin}
        self.close = AsyncMock()


def _embed_session():
    return SimpleNamespace(expires_at=None, embed_token_id=1, workflow_run_id=42)


def _embed_token(allowed_domains):
    return SimpleNamespace(allowed_domains=allowed_domains, created_by=7, workflow_id=3)


def _patch_deps():
    """Patch db_client + signaling_manager for a valid, non-expired session."""
    db = patch("api.routes.webrtc_signaling.db_client").start()
    mgr = patch("api.routes.webrtc_signaling.signaling_manager").start()
    db.get_embed_session_by_token = AsyncMock(return_value=_embed_session())
    db.get_embed_token_by_id = AsyncMock(return_value=_embed_token(["example.com"]))
    db.get_user_by_id = AsyncMock(return_value=SimpleNamespace(id=7))
    mgr.handle_websocket = AsyncMock()
    return db, mgr


@pytest.mark.asyncio
async def test_public_signaling_rejects_disallowed_origin():
    from api.routes.webrtc_signaling import public_signaling_websocket

    ws = _FakeWebSocket("https://evil.example")
    _db, mgr = _patch_deps()
    try:
        await public_signaling_websocket(ws, "emb_session_tok")
    finally:
        patch.stopall()

    # Regression (issue #330): a valid session token presented from an origin
    # outside the embed allowlist must be rejected before the signaling handoff.
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs.get("code") == 1008
    mgr.handle_websocket.assert_not_called()


@pytest.mark.asyncio
async def test_public_signaling_accepts_allowed_origin():
    from api.routes.webrtc_signaling import public_signaling_websocket

    ws = _FakeWebSocket("https://example.com")
    _db, mgr = _patch_deps()
    try:
        await public_signaling_websocket(ws, "emb_session_tok")
    finally:
        patch.stopall()

    # An origin within the allowlist proceeds to the signaling handoff.
    mgr.handle_websocket.assert_awaited_once()
