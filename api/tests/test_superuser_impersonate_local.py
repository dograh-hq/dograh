"""Tests for superadmin impersonation in local (OSS email/password) auth mode.

``POST /superuser/impersonate`` branches on ``AUTH_PROVIDER``: local mode
mints an OSS HS256 JWT for the target user (``provider: "local"``); stack
mode keeps calling Stack Auth exactly as before. ``AUTH_PROVIDER`` is
patched at the route module attribute so the tests are independent of the
environment.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.superuser import router
from api.services.auth.depends import get_superuser
from api.utils.auth import decode_jwt_token


def _superuser():
    return SimpleNamespace(id=1, is_superuser=True, selected_organization_id=99)


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_superuser] = _superuser
    return app


def _target_user(**overrides):
    defaults = {
        "id": 42,
        "provider_id": "oss_42_uuid",
        "email": "client@example.test",
        "selected_organization_id": 7,
        "is_superuser": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ======== AUTHZ ========


def test_impersonate_returns_403_for_non_superuser():
    app = FastAPI()
    app.include_router(router)  # no overrides — real get_superuser runs
    client = TestClient(app)

    non_superuser = SimpleNamespace(id=2, is_superuser=False)
    with patch(
        "api.services.auth.depends.get_user",
        new=AsyncMock(return_value=non_superuser),
    ):
        response = client.post("/superuser/impersonate", json={"user_id": 42})

    assert response.status_code == 403


# ======== LOCAL MODE ========


def test_local_impersonate_by_user_id_mints_jwt_with_target_sub():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.superuser.AUTH_PROVIDER", "local"),
        patch("api.routes.superuser.db_client") as db,
    ):
        db.get_user_by_id = AsyncMock(return_value=_target_user())

        response = client.post("/superuser/impersonate", json={"user_id": 42})

    assert response.status_code == 200
    db.get_user_by_id.assert_awaited_once_with(42)
    body = response.json()
    assert body["provider"] == "local"
    assert body["refresh_token"] is None
    assert decode_jwt_token(body["access_token"])["sub"] == "42"
    assert body["user"]["id"] == 42
    assert body["user"]["email"] == "client@example.test"
    assert body["user"]["organization_id"] == 7
    assert body["user"]["is_superuser"] is False


def test_local_impersonate_by_provider_id():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.superuser.AUTH_PROVIDER", "local"),
        patch("api.routes.superuser.db_client") as db,
    ):
        db.get_user_by_provider_id = AsyncMock(return_value=_target_user())

        response = client.post(
            "/superuser/impersonate", json={"provider_user_id": "oss_42_uuid"}
        )

    assert response.status_code == 200
    db.get_user_by_provider_id.assert_awaited_once_with("oss_42_uuid")
    assert decode_jwt_token(response.json()["access_token"])["sub"] == "42"


def test_local_impersonate_falls_back_to_email_lookup():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.superuser.AUTH_PROVIDER", "local"),
        patch("api.routes.superuser.db_client") as db,
    ):
        db.get_user_by_provider_id = AsyncMock(return_value=None)
        db.get_user_by_email = AsyncMock(return_value=_target_user())

        response = client.post(
            "/superuser/impersonate",
            json={"provider_user_id": "client@example.test"},
        )

    assert response.status_code == 200
    db.get_user_by_email.assert_awaited_once_with("client@example.test")
    assert decode_jwt_token(response.json()["access_token"])["sub"] == "42"


def test_local_impersonate_404_when_target_missing():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.superuser.AUTH_PROVIDER", "local"),
        patch("api.routes.superuser.db_client") as db,
    ):
        db.get_user_by_id = AsyncMock(return_value=None)
        missing_by_id = client.post("/superuser/impersonate", json={"user_id": 7})

        db.get_user_by_provider_id = AsyncMock(return_value=None)
        missing_by_provider = client.post(
            "/superuser/impersonate", json={"provider_user_id": "nope"}
        )

    assert missing_by_id.status_code == 404
    assert missing_by_provider.status_code == 404


def test_local_impersonate_400_without_identifiers():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.superuser.AUTH_PROVIDER", "local"):
        response = client.post("/superuser/impersonate", json={})

    assert response.status_code == 400


def test_local_impersonate_refuses_other_superusers_but_allows_self():
    app = _make_test_app()
    client = TestClient(app)

    other_superuser = _target_user(id=42, is_superuser=True)
    self_superuser = _target_user(id=1, is_superuser=True, email="admin@example.test")
    with (
        patch("api.routes.superuser.AUTH_PROVIDER", "local"),
        patch("api.routes.superuser.db_client") as db,
    ):
        db.get_user_by_id = AsyncMock(return_value=other_superuser)
        refused = client.post("/superuser/impersonate", json={"user_id": 42})

        db.get_user_by_id = AsyncMock(return_value=self_superuser)
        allowed = client.post("/superuser/impersonate", json={"user_id": 1})

    assert refused.status_code == 403
    assert allowed.status_code == 200
    assert decode_jwt_token(allowed.json()["access_token"])["sub"] == "1"


# ======== STACK MODE (regression: path unchanged) ========


def test_stack_mode_still_calls_stack_auth():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.superuser.AUTH_PROVIDER", "stack"),
        patch(
            "api.routes.superuser.stackauth.impersonate",
            new=AsyncMock(
                return_value={"refresh_token": "rt", "access_token": "at"}
            ),
        ) as stack_impersonate,
    ):
        response = client.post(
            "/superuser/impersonate", json={"provider_user_id": "stack-uuid"}
        )

    assert response.status_code == 200
    stack_impersonate.assert_awaited_once_with("stack-uuid")
    body = response.json()
    assert body["provider"] == "stack"
    assert body["refresh_token"] == "rt"
    assert body["access_token"] == "at"
