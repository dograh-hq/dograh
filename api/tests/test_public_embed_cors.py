from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.public_embed import router

app = FastAPI()
app.include_router(router, prefix="/api/v1")
client = TestClient(app, raise_server_exceptions=False)

_ACTIVE_TOKEN = SimpleNamespace(
    is_active=True,
    expires_at=None,
    allowed_domains=[],
    workflow_id=1,
    settings={},
)

_RESTRICTED_TOKEN = SimpleNamespace(
    is_active=True,
    expires_at=None,
    allowed_domains=["allowed.example.com"],
    workflow_id=2,
    settings={},
)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    async def _get_token(token):
        if token == "valid":
            return _ACTIVE_TOKEN
        if token == "restricted":
            return _RESTRICTED_TOKEN
        return None

    monkeypatch.setattr(
        "api.routes.public_embed.db_client.get_embed_token_by_token",
        _get_token,
    )


def test_options_config_returns_acao_for_allowed_origin():
    resp = client.options(
        "/api/v1/public/embed/config/valid",
        headers={"Origin": "https://mysite.vercel.app"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://mysite.vercel.app"


def test_options_config_rejects_unknown_token():
    resp = client.options(
        "/api/v1/public/embed/config/unknown",
        headers={"Origin": "https://mysite.vercel.app"},
    )
    assert resp.status_code == 403


def test_options_config_rejects_disallowed_origin():
    resp = client.options(
        "/api/v1/public/embed/config/restricted",
        headers={"Origin": "https://notallowed.example.com"},
    )
    assert resp.status_code == 403


def test_get_config_includes_acao_header():
    resp = client.get(
        "/api/v1/public/embed/config/valid",
        headers={"Origin": "https://mysite.vercel.app"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://mysite.vercel.app"


def test_get_config_rejects_disallowed_origin():
    resp = client.get(
        "/api/v1/public/embed/config/restricted",
        headers={"Origin": "https://notallowed.example.com"},
    )
    assert resp.status_code == 403
