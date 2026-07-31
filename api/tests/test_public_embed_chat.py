"""Tests for the public embed chat surface.

Covers the /init widget-type branch (chat vs voice), the public chat message
endpoints, and — most importantly — the allowlist projection: public responses
must never carry checkpoint (serialized LLM context incl. system prompt),
events (raw exception text), usage, or run context fields.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from api.routes.public_embed import PublicEmbedCORSMiddleware
from api.routes.public_embed import router as public_embed_router
from api.routes.public_embed_chat import router as public_embed_chat_router
from api.services.workflow.embed_context import MAX_VALUE_LENGTH
from api.services.workflow.embed_session_service import (
    authorize_embed_workflow_run_start,
)
from api.services.workflow.embed_text_chat_service import (
    EMBED_CHAT_MAX_TURNS,
    EmbedChatTurnLimitExceededError,
    append_embed_text_chat_message,
    build_public_chat_session_response,
)
from api.services.workflow.text_chat_session_service import (
    TextChatSessionExecutionError,
    TextChatSessionRevisionConflictError,
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.dograh.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(PublicEmbedCORSMiddleware, api_prefix="/api/v1")
app.include_router(public_embed_router, prefix="/api/v1")
app.include_router(public_embed_chat_router, prefix="/api/v1")
client = TestClient(app, raise_server_exceptions=False)

ORIGIN = "https://mysite.vercel.app"

_CHAT_TOKEN = SimpleNamespace(
    id=10,
    is_active=True,
    expires_at=None,
    allowed_domains=[],
    workflow_id=1,
    organization_id=11,
    created_by=7,
    usage_limit=None,
    usage_count=0,
    settings={"widgetType": "chat"},
)

_VOICE_TOKEN = SimpleNamespace(
    id=20,
    is_active=True,
    expires_at=None,
    allowed_domains=[],
    workflow_id=1,
    organization_id=11,
    created_by=7,
    usage_limit=None,
    usage_count=0,
    settings={"widgetType": "voice"},
)

_RESTRICTED_TOKEN = SimpleNamespace(
    id=30,
    is_active=True,
    expires_at=None,
    allowed_domains=["allowed.example.com"],
    workflow_id=1,
    organization_id=11,
    created_by=7,
    usage_limit=None,
    usage_count=0,
    settings={"widgetType": "chat"},
)

_INACTIVE_TOKEN = SimpleNamespace(
    id=40,
    is_active=False,
    expires_at=None,
    allowed_domains=[],
    workflow_id=1,
    organization_id=11,
    created_by=7,
    usage_limit=None,
    usage_count=0,
    settings={"widgetType": "chat"},
)

_TOKENS_BY_ID = {
    t.id: t for t in (_CHAT_TOKEN, _VOICE_TOKEN, _RESTRICTED_TOKEN, _INACTIVE_TOKEN)
}

# A turn as persisted by the session service — carrying everything the public
# response must strip.
_SENSITIVE_TURN = {
    "id": "turn_abc123",
    "status": "completed",
    "created_at": "2026-07-31T00:00:00+00:00",
    "user_message": {"text": "hi", "created_at": "2026-07-31T00:00:00+00:00"},
    "assistant_message": {
        "text": "Hello! How can I help?",
        "created_at": "2026-07-31T00:00:01+00:00",
    },
    "events": [
        {
            "type": "execution_error",
            "payload": {"message": "Traceback: secret internals"},
        }
    ],
    "usage": {"prompt_tokens": 42},
    "checkpoint_after_turn": {
        "messages": [{"role": "system", "content": "SECRET SYSTEM PROMPT"}]
    },
}

_GREETING_TURN = {
    "id": "turn_greet01",
    "status": "completed",
    "created_at": "2026-07-31T00:00:00+00:00",
    "user_message": None,
    "assistant_message": {
        "text": "Hi, I'm your agent.",
        "created_at": "2026-07-31T00:00:01+00:00",
    },
    "events": [],
    "usage": {},
    "checkpoint_after_turn": {"messages": []},
}


def _text_session(
    *,
    turns=None,
    revision=3,
    state="running",
    is_completed=False,
    workflow_id=1,
    mode="textchat",
    run_id=123,
):
    return SimpleNamespace(
        revision=revision,
        session_data={"turns": list(turns or [])},
        workflow_run=SimpleNamespace(
            id=run_id,
            workflow_id=workflow_id,
            mode=mode,
            state=state,
            is_completed=is_completed,
        ),
    )


class _QuotaResult(SimpleNamespace):
    pass


def _quota(has_quota=True):
    return _QuotaResult(has_quota=has_quota, error_message="quota exceeded")


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    async def _get_token(token):
        return {
            "chat": _CHAT_TOKEN,
            "voice": _VOICE_TOKEN,
            "restricted": _RESTRICTED_TOKEN,
            "inactive": _INACTIVE_TOKEN,
        }.get(token)

    async def _get_token_by_id(token_id):
        return _TOKENS_BY_ID.get(token_id)

    async def _get_session(session_token):
        if session_token == "session-chat":
            return SimpleNamespace(
                embed_token_id=_CHAT_TOKEN.id, expires_at=None, workflow_run_id=123
            )
        if session_token == "session-restricted":
            return SimpleNamespace(
                embed_token_id=_RESTRICTED_TOKEN.id,
                expires_at=None,
                workflow_run_id=123,
            )
        if session_token == "session-inactive":
            return SimpleNamespace(
                embed_token_id=_INACTIVE_TOKEN.id, expires_at=None, workflow_run_id=123
            )
        if session_token == "session-expired":
            return SimpleNamespace(
                embed_token_id=_CHAT_TOKEN.id,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
                workflow_run_id=123,
            )
        return None

    created_runs = []

    async def _create_workflow_run(**kwargs):
        created_runs.append(kwargs)
        return SimpleNamespace(id=123)

    async def _update_workflow_run(_run_id, **_kwargs):
        return SimpleNamespace(id=123)

    async def _get_workflow(*_args, **_kwargs):
        return SimpleNamespace(
            id=1,
            released_definition=SimpleNamespace(id=55, template_context_variables={}),
            current_definition=None,
            template_context_variables={},
        )

    async def _get_user(_user_id):
        return SimpleNamespace(id=7)

    async def _noop(*_args, **_kwargs):
        return None

    async def _allow(*_args, **_kwargs):
        return True

    for target, fake in [
        ("get_embed_token_by_token", _get_token),
        ("get_embed_token_by_id", _get_token_by_id),
        ("get_embed_session_by_token", _get_session),
        ("create_workflow_run", _create_workflow_run),
        ("update_workflow_run", _update_workflow_run),
        ("get_workflow", _get_workflow),
        ("get_user_by_id", _get_user),
        ("create_embed_session", _noop),
        ("reserve_embed_token_usage", _allow),
    ]:
        monkeypatch.setattr(f"api.routes.public_embed.db_client.{target}", fake)
    monkeypatch.setattr(
        "api.services.workflow.embed_session_service.db_client.get_user_by_id",
        _get_user,
    )

    async def _authorize(**_kwargs):
        return _quota(True)

    monkeypatch.setattr(
        "api.services.workflow.embed_session_service.authorize_workflow_run_start",
        _authorize,
    )
    monkeypatch.setattr("api.routes.public_embed.allow_embed_chat_init", _allow)
    monkeypatch.setattr(
        "api.services.workflow.embed_text_chat_service.allow_embed_chat_message",
        _allow,
    )

    async def _start_chat(**_kwargs):
        return _text_session(turns=[_GREETING_TURN], revision=2)

    monkeypatch.setattr("api.routes.public_embed.start_embed_text_chat", _start_chat)

    yield SimpleNamespace(created_runs=created_runs)


def _patch_chat_text_session(monkeypatch, text_session):
    async def _get(_run_id, organization_id=None):
        assert organization_id == 11
        return text_session

    monkeypatch.setattr(
        "api.services.workflow.embed_text_chat_service.db_client.get_workflow_run_text_session",
        _get,
    )


def _assert_embed_cors(resp, origin: str):
    assert resp.headers.get("access-control-allow-origin") == origin
    assert "origin" in {
        value.strip().lower() for value in resp.headers.get("vary", "").split(",")
    }


# ---------------------------------------------------------------------------
# /init widget-type branching
# ---------------------------------------------------------------------------


def test_init_chat_token_creates_textchat_run(_patch_db):
    resp = client.post(
        "/api/v1/public/embed/init",
        headers={"Origin": ORIGIN},
        json={"token": "chat", "context_variables": {"name": "Ada"}},
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, ORIGIN)

    assert len(_patch_db.created_runs) == 1
    kwargs = _patch_db.created_runs[0]
    assert kwargs["mode"] == "textchat"
    # Text-chat runs must not carry a webrtc transport provider.
    assert "provider" not in kwargs["initial_context"]
    assert kwargs["initial_context"]["name"] == "Ada"

    body = resp.json()
    assert body["widget_type"] == "chat"
    assert body["chat_session"]["revision"] == 2
    assert (
        body["chat_session"]["turns"][0]["assistant_message"]["text"]
        == "Hi, I'm your agent."
    )


def test_init_voice_token_keeps_smallwebrtc(_patch_db):
    resp = client.post(
        "/api/v1/public/embed/init",
        headers={"Origin": ORIGIN},
        json={"token": "voice"},
    )
    assert resp.status_code == 200

    kwargs = _patch_db.created_runs[0]
    assert kwargs["mode"] == "smallwebrtc"
    assert kwargs["initial_context"]["provider"] == "smallwebrtc"

    body = resp.json()
    assert body["widget_type"] == "voice"
    assert body["chat_session"] is None


def test_init_sanitizes_context_variables(_patch_db):
    resp = client.post(
        "/api/v1/public/embed/init",
        headers={"Origin": ORIGIN},
        json={
            "token": "voice",
            "context_variables": {
                "  customer_name  ": "Abhishek",
                "provider": "twilio",
                "bio": "a" * (MAX_VALUE_LENGTH + 500),
            },
        },
    )
    assert resp.status_code == 200

    initial_context = _patch_db.created_runs[0]["initial_context"]
    assert initial_context["customer_name"] == "Abhishek"
    # The host page can't rewrite the transport the backend picked.
    assert initial_context["provider"] == "smallwebrtc"
    assert len(initial_context["bio"]) == MAX_VALUE_LENGTH


def test_init_rejects_exhausted_usage_reservation(monkeypatch, _patch_db):
    async def _exhausted(*_args, **_kwargs):
        return False

    monkeypatch.setattr(
        "api.routes.public_embed.db_client.reserve_embed_token_usage", _exhausted
    )

    resp = client.post(
        "/api/v1/public/embed/init",
        headers={"Origin": ORIGIN},
        json={"token": "chat"},
    )

    assert resp.status_code == 403
    assert _patch_db.created_runs == []


def test_init_chat_rate_limit_429(monkeypatch, _patch_db):
    async def _rate_limited(_embed_token_id):
        return False

    monkeypatch.setattr("api.routes.public_embed.allow_embed_chat_init", _rate_limited)

    resp = client.post(
        "/api/v1/public/embed/init",
        headers={"Origin": ORIGIN},
        json={"token": "chat"},
    )

    assert resp.status_code == 429
    assert _patch_db.created_runs == []


def test_init_chat_quota_exhausted_402(monkeypatch):
    async def _authorize(**_kwargs):
        return _quota(False)

    started = []

    async def _start_chat(**kwargs):
        started.append(kwargs)
        return _text_session()

    monkeypatch.setattr(
        "api.routes.public_embed.authorize_embed_workflow_run_start", _authorize
    )
    monkeypatch.setattr("api.routes.public_embed.start_embed_text_chat", _start_chat)

    resp = client.post(
        "/api/v1/public/embed/init",
        headers={"Origin": ORIGIN},
        json={"token": "chat"},
    )
    assert resp.status_code == 402
    # Quota is checked before any LLM spend.
    assert started == []


def test_init_chat_greeting_failure_returns_generic_500(monkeypatch):
    async def _start_chat(**_kwargs):
        raise TextChatSessionExecutionError("Traceback: secret internals")

    monkeypatch.setattr("api.routes.public_embed.start_embed_text_chat", _start_chat)

    resp = client.post(
        "/api/v1/public/embed/init",
        headers={"Origin": ORIGIN},
        json={"token": "chat"},
    )
    assert resp.status_code == 500
    assert "secret" not in resp.text


# ---------------------------------------------------------------------------
# Public projection (the security boundary)
# ---------------------------------------------------------------------------


def test_get_chat_session_returns_lean_projection(monkeypatch):
    _patch_chat_text_session(
        monkeypatch, _text_session(turns=[_SENSITIVE_TURN], revision=5)
    )

    resp = client.get(
        "/api/v1/public/embed/chat/session-chat",
        headers={"Origin": ORIGIN},
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, ORIGIN)

    body = resp.json()
    assert set(body.keys()) == {"revision", "state", "is_completed", "turns"}
    assert body["revision"] == 5
    assert body["state"] == "running"
    assert body["is_completed"] is False

    turn = body["turns"][0]
    assert set(turn.keys()) == {"id", "status", "user_message", "assistant_message"}
    assert set(turn["user_message"].keys()) == {"text", "created_at"}
    assert set(turn["assistant_message"].keys()) == {"text", "created_at"}
    # Nothing sensitive anywhere in the payload.
    assert "SECRET" not in resp.text
    assert "Traceback" not in resp.text


def test_projection_builder_strips_sensitive_fields():
    response = build_public_chat_session_response(
        _text_session(turns=[_SENSITIVE_TURN, _GREETING_TURN])
    )
    dumped = response.model_dump()
    assert set(dumped.keys()) == {"revision", "state", "is_completed", "turns"}
    for turn in dumped["turns"]:
        assert set(turn.keys()) == {"id", "status", "user_message", "assistant_message"}
    # The greeting turn has no user message.
    assert dumped["turns"][1]["user_message"] is None


# ---------------------------------------------------------------------------
# POST /messages
# ---------------------------------------------------------------------------


def test_post_message_happy_path(monkeypatch):
    _patch_chat_text_session(monkeypatch, _text_session(turns=[_GREETING_TURN]))

    appended = []

    async def _append(**kwargs):
        appended.append(kwargs)
        reply_turn = {
            **_SENSITIVE_TURN,
            "id": "turn_reply1",
            "user_message": {
                "text": "hello",
                "created_at": "2026-07-31T00:01:00+00:00",
            },
        }
        return _text_session(turns=[_GREETING_TURN, reply_turn], revision=4)

    monkeypatch.setattr(
        "api.services.workflow.embed_text_chat_service.append_embed_text_chat_message",
        _append,
    )

    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello", "expected_revision": 3},
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, ORIGIN)

    assert appended[0]["text"] == "hello"
    assert appended[0]["expected_revision"] == 3

    body = resp.json()
    assert body["revision"] == 4
    assert len(body["turns"]) == 2
    assert "SECRET" not in resp.text


def test_post_message_revision_conflict_409(monkeypatch):
    _patch_chat_text_session(monkeypatch, _text_session())

    async def _append(**_kwargs):
        raise TextChatSessionRevisionConflictError(
            expected_revision=3, actual_revision=5
        )

    monkeypatch.setattr(
        "api.services.workflow.embed_text_chat_service.append_embed_text_chat_message",
        _append,
    )

    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello", "expected_revision": 3},
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["expected_revision"] == 3
    assert detail["actual_revision"] == 5


def test_post_message_rate_limit_429(monkeypatch):
    _patch_chat_text_session(monkeypatch, _text_session())

    async def _rate_limited(_workflow_run_id):
        return False

    monkeypatch.setattr(
        "api.services.workflow.embed_text_chat_service.allow_embed_chat_message",
        _rate_limited,
    )

    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello", "expected_revision": 3},
    )

    assert resp.status_code == 429


def test_post_message_turn_cap_429(monkeypatch):
    _patch_chat_text_session(monkeypatch, _text_session())

    async def _append(**_kwargs):
        raise EmbedChatTurnLimitExceededError()

    monkeypatch.setattr(
        "api.services.workflow.embed_text_chat_service.append_embed_text_chat_message",
        _append,
    )

    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello"},
    )
    assert resp.status_code == 429


def test_post_message_quota_402(monkeypatch):
    _patch_chat_text_session(monkeypatch, _text_session())

    async def _authorize(**_kwargs):
        return _quota(False)

    monkeypatch.setattr(
        "api.services.workflow.embed_text_chat_service.authorize_embed_workflow_run_start",
        _authorize,
    )

    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello"},
    )
    assert resp.status_code == 402


def test_post_message_execution_error_generic_500(monkeypatch):
    _patch_chat_text_session(monkeypatch, _text_session())

    async def _append(**_kwargs):
        raise TextChatSessionExecutionError("Traceback: secret internals")

    monkeypatch.setattr(
        "api.services.workflow.embed_text_chat_service.append_embed_text_chat_message",
        _append,
    )

    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello"},
    )
    assert resp.status_code == 500
    assert "secret" not in resp.text


def test_post_message_completed_run_400(monkeypatch):
    _patch_chat_text_session(monkeypatch, _text_session(is_completed=True))

    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "chat_completed"


def test_post_message_voice_run_400(monkeypatch):
    _patch_chat_text_session(monkeypatch, _text_session(mode="smallwebrtc"))

    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello"},
    )
    assert resp.status_code == 400


def test_post_message_expired_session_403():
    resp = client.post(
        "/api/v1/public/embed/chat/session-expired/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello"},
    )
    assert resp.status_code == 403
    # Error responses must be CORS-readable so the widget can distinguish an
    # expired session from a network failure (raised HTTPExceptions bypass the
    # route's Response object; the middleware reflects ACAO instead).
    _assert_embed_cors(resp, ORIGIN)


def test_options_expired_session_preflight_succeeds():
    # Session-state problems must not fail the preflight — the browser would
    # surface them as an opaque network error. The real request returns the
    # readable 403 instead.
    resp = client.options(
        "/api/v1/public/embed/chat/session-expired/messages",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    _assert_embed_cors(resp, ORIGIN)


def test_post_message_invalid_session_404():
    resp = client.post(
        "/api/v1/public/embed/chat/session-unknown/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello"},
    )
    assert resp.status_code == 404


def test_post_message_inactive_token_403():
    resp = client.post(
        "/api/v1/public/embed/chat/session-inactive/messages",
        headers={"Origin": ORIGIN},
        json={"text": "hello"},
    )
    assert resp.status_code == 403


def test_post_message_disallowed_origin_403():
    resp = client.post(
        "/api/v1/public/embed/chat/session-restricted/messages",
        headers={"Origin": "https://notallowed.example.com"},
        json={"text": "hello"},
    )
    assert resp.status_code == 403


def test_post_message_text_too_long_422():
    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": "x" * 4001},
    )
    assert resp.status_code == 422


def test_post_message_empty_text_422():
    resp = client.post(
        "/api/v1/public/embed/chat/session-chat/messages",
        headers={"Origin": ORIGIN},
        json={"text": ""},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Service authorization and turn cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_authorization_resolves_token_creator_with_org_scope(monkeypatch):
    actor = SimpleNamespace(id=7)
    captured = {}

    async def _get_user(user_id):
        assert user_id == _CHAT_TOKEN.created_by
        return actor

    async def _authorize(**kwargs):
        captured.update(kwargs)
        return _quota(True)

    monkeypatch.setattr(
        "api.services.workflow.embed_session_service.db_client.get_user_by_id",
        _get_user,
    )
    monkeypatch.setattr(
        "api.services.workflow.embed_session_service.authorize_workflow_run_start",
        _authorize,
    )

    result = await authorize_embed_workflow_run_start(
        embed_token=_CHAT_TOKEN, workflow_run_id=123
    )

    assert result.has_quota is True
    assert captured == {
        "workflow_id": _CHAT_TOKEN.workflow_id,
        "organization_id": _CHAT_TOKEN.organization_id,
        "workflow_run_id": 123,
        "actor_user": actor,
    }


@pytest.mark.asyncio
async def test_append_embed_message_enforces_turn_cap():
    full_session = _text_session(
        turns=[
            {**_GREETING_TURN, "id": f"turn_{i}"} for i in range(EMBED_CHAT_MAX_TURNS)
        ]
    )
    # The cap check fires before any DB or LLM work.
    with pytest.raises(EmbedChatTurnLimitExceededError):
        await append_embed_text_chat_message(
            workflow_id=1,
            run_id=123,
            text_session=full_session,
            text="one more",
            expected_revision=3,
        )


# ---------------------------------------------------------------------------
# Turn-credentials regression after the resolve_embed_session refactor
# ---------------------------------------------------------------------------


def test_turn_credentials_rejects_inactive_token(monkeypatch):
    # Deliberate tightening: the shared resolve_embed_session chain now checks
    # embed_token.is_active on the turn-credentials path too.
    monkeypatch.setattr("api.routes.public_embed.ENABLE_COTURN", True)
    monkeypatch.setattr("api.routes.public_embed.TURN_SECRET", "test-secret")

    resp = client.get(
        "/api/v1/public/embed/turn-credentials/session-inactive",
        headers={"Origin": ORIGIN},
    )
    assert resp.status_code == 403
