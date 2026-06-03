from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from api.routes.public_embed import PublicEmbedCORSMiddleware, router

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.dograh.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(PublicEmbedCORSMiddleware, api_prefix="/api/v1")
app.include_router(router, prefix="/api/v1")
client = TestClient(app, raise_server_exceptions=False)

_ACTIVE_TOKEN = SimpleNamespace(
    id=10,
    is_active=True,
    expires_at=None,
    allowed_domains=[],
    workflow_id=1,
    created_by=7,
    usage_limit=None,
    usage_count=0,
    settings={},
)

_RESTRICTED_TOKEN = SimpleNamespace(
    id=20,
    is_active=True,
    expires_at=None,
    allowed_domains=["allowed.example.com"],
    workflow_id=2,
    created_by=7,
    usage_limit=None,
    usage_count=0,
    settings={},
)

_LOCALHOST_TOKEN = SimpleNamespace(
    id=30,
    is_active=True,
    expires_at=None,
    allowed_domains=["localhost:3000", "localhost:3020"],
    workflow_id=3,
    created_by=7,
    usage_limit=None,
    usage_count=0,
    settings={},
)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    async def _get_token(token):
        if token == "valid":
            return _ACTIVE_TOKEN
        if token == "restricted":
            return _RESTRICTED_TOKEN
        if token == "localhost":
            return _LOCALHOST_TOKEN
        return None

    async def _get_token_by_id(token_id):
        if token_id == _ACTIVE_TOKEN.id:
            return _ACTIVE_TOKEN
        if token_id == _RESTRICTED_TOKEN.id:
            return _RESTRICTED_TOKEN
        if token_id == _LOCALHOST_TOKEN.id:
            return _LOCALHOST_TOKEN
        return None

    async def _get_session(session_token):
        if session_token == "session-valid":
            return SimpleNamespace(embed_token_id=_ACTIVE_TOKEN.id, expires_at=None)
        if session_token == "session-restricted":
            return SimpleNamespace(embed_token_id=_RESTRICTED_TOKEN.id, expires_at=None)
        return None

    async def _create_workflow_run(**_kwargs):
        return SimpleNamespace(id=123)

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "api.routes.public_embed.db_client.get_embed_token_by_token",
        _get_token,
    )
    monkeypatch.setattr(
        "api.routes.public_embed.db_client.get_embed_token_by_id",
        _get_token_by_id,
    )
    monkeypatch.setattr(
        "api.routes.public_embed.db_client.get_embed_session_by_token",
        _get_session,
    )
    monkeypatch.setattr(
        "api.routes.public_embed.db_client.create_workflow_run",
        _create_workflow_run,
    )
    monkeypatch.setattr(
        "api.routes.public_embed.db_client.create_embed_session",
        _noop,
    )
    monkeypatch.setattr(
        "api.routes.public_embed.db_client.increment_embed_token_usage",
        _noop,
    )
    monkeypatch.setattr("api.routes.public_embed.TURN_SECRET", "test-secret")
    monkeypatch.setattr(
        "api.routes.public_embed.generate_turn_credentials",
        lambda _user_id: {
            "username": "turn-user",
            "password": "turn-password",
            "ttl": 3600,
            "uris": ["turn:example.com:3478"],
        },
    )


def _assert_embed_cors(resp, origin: str):
    assert resp.headers.get("access-control-allow-origin") == origin
    assert "origin" in {
        value.strip().lower() for value in resp.headers.get("vary", "").split(",")
    }


def test_options_config_returns_acao_for_allowed_origin():
    origin = "https://mysite.vercel.app"
    resp = client.options(
        "/api/v1/public/embed/config/valid",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, origin)


def test_options_config_accepts_allowed_localhost_port():
    origin = "http://localhost:3020"
    resp = client.options(
        "/api/v1/public/embed/config/localhost",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, origin)


def test_options_config_rejects_unknown_token():
    resp = client.options(
        "/api/v1/public/embed/config/unknown",
        headers={
            "Origin": "https://mysite.vercel.app",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 403


def test_options_config_rejects_disallowed_origin():
    resp = client.options(
        "/api/v1/public/embed/config/restricted",
        headers={
            "Origin": "https://notallowed.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 403


def test_get_config_includes_acao_header():
    origin = "https://mysite.vercel.app"
    resp = client.get(
        "/api/v1/public/embed/config/valid",
        headers={"Origin": origin},
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, origin)


def test_get_config_accepts_allowed_localhost_port():
    origin = "http://localhost:3020"
    resp = client.get(
        "/api/v1/public/embed/config/localhost",
        headers={"Origin": origin},
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, origin)


def test_get_config_rejects_unlisted_localhost_port():
    resp = client.get(
        "/api/v1/public/embed/config/localhost",
        headers={"Origin": "http://localhost:3021"},
    )
    assert resp.status_code == 403


def test_get_config_rejects_disallowed_origin():
    resp = client.get(
        "/api/v1/public/embed/config/restricted",
        headers={"Origin": "https://notallowed.example.com"},
    )
    assert resp.status_code == 403


def test_init_includes_acao_header():
    origin = "https://mysite.vercel.app"
    resp = client.post(
        "/api/v1/public/embed/init",
        headers={"Origin": origin},
        json={"token": "valid"},
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, origin)


def test_turn_credentials_includes_acao_header():
    origin = "https://mysite.vercel.app"
    resp = client.get(
        "/api/v1/public/embed/turn-credentials/session-valid",
        headers={"Origin": origin},
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, origin)


def test_options_init_returns_acao_for_allowed_origin():
    origin = "https://mysite.vercel.app"
    resp = client.options(
        "/api/v1/public/embed/init",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, origin)


def test_options_turn_credentials_returns_acao_for_allowed_origin():
    origin = "https://mysite.vercel.app"
    resp = client.options(
        "/api/v1/public/embed/turn-credentials/session-valid",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, origin)


def test_options_turn_credentials_rejects_disallowed_origin():
    resp = client.options(
        "/api/v1/public/embed/turn-credentials/session-restricted",
        headers={
            "Origin": "https://notallowed.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 403
