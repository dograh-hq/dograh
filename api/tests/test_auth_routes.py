from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.auth as auth_routes
import api.schemas.auth as auth_schemas
from api.routes.auth import router
from api.services.auth import depends as auth_depends
from api.services.auth.depends import get_user


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_stack_mode_hides_email_password_auth_routes(monkeypatch):
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "stack")
    client = TestClient(_make_test_app())

    signup_response = client.post(
        "/auth/signup",
        json={
            "email": "user@example.com",
            "password": "password123",
            "name": "User",
        },
    )
    login_response = client.post(
        "/auth/login",
        json={
            "email": "user@example.com",
            "password": "password123",
        },
    )

    assert signup_response.status_code == 404
    assert signup_response.json() == {"detail": "Not found"}
    assert login_response.status_code == 404
    assert login_response.json() == {"detail": "Not found"}


def test_signup_disabled_returns_403(monkeypatch):
    monkeypatch.setattr(auth_routes, "ENABLE_SIGNUP", False)
    client = TestClient(_make_test_app())

    response = client.post(
        "/auth/signup",
        json={
            "email": "user@example.com",
            "password": "password123",
            "name": "User",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Signup is disabled"}


def test_stack_mode_keeps_current_user_route_available(monkeypatch):
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "stack")
    app = _make_test_app()
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=7,
        email="user@example.com",
        selected_organization_id=42,
        provider_id="stack-user-1",
    )
    client = TestClient(app)

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": 7,
        "email": "user@example.com",
        "name": None,
        "organization_id": 42,
        "provider_id": "stack-user-1",
    }


def test_local_auth_accepts_reserved_test_domain_for_smoke_users(monkeypatch):
    monkeypatch.setattr(auth_schemas, "AUTH_PROVIDER", "local")

    signup = auth_schemas.SignupRequest(
        email=" smokescreen.local@example.test ",
        password="local-password",
    )
    login = auth_schemas.LoginRequest(
        email="testuser.local@example.test",
        password="local-password",
    )

    assert signup.email == "smokescreen.local@example.test"
    assert login.email == "testuser.local@example.test"


def test_non_local_auth_keeps_reserved_test_domain_rejected(monkeypatch):
    monkeypatch.setattr(auth_schemas, "AUTH_PROVIDER", "stack")

    try:
        auth_schemas.LoginRequest(
            email="smokescreen.local@example.test",
            password="local-password",
        )
    except ValueError as exc:
        assert "special-use" in str(exc)
    else:
        raise AssertionError("reserved .test email must remain rejected in hosted auth")
