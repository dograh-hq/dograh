"""Tests for the superuser admin Clients routes.

Same conventions as test_telephony_routes: a minimal FastAPI app with the
router mounted, ``get_superuser`` overridden for happy paths, and the DB
layer patched at the route module's ``db_client`` attribute.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.admin_clients import router
from api.services.auth.depends import get_superuser

DEFAULT_API_BASE = "https://app.voicelink.co.in/api"


def _superuser():
    return SimpleNamespace(id=1, is_superuser=True, selected_organization_id=99)


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_superuser] = _superuser
    return app


def _org(**overrides):
    defaults = {
        "id": 5,
        "provider_id": "org_oss_abc",
        "created_at": None,
        "voicelink_status": "pending",
        "voicelink_client_id": None,
        "voicelink_username": "jane.5",
        "voicelink_error": "No channels available",
        "users": [
            SimpleNamespace(id=9, provider_id="oss_abc", email="jane@example.test")
        ],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ======== AUTHZ ========


def test_endpoints_return_403_for_non_superuser():
    app = FastAPI()
    app.include_router(router)  # no overrides — real get_superuser runs
    client = TestClient(app)

    non_superuser = SimpleNamespace(id=2, is_superuser=False)
    with patch(
        "api.services.auth.depends.get_user",
        new=AsyncMock(return_value=non_superuser),
    ):
        list_response = client.get("/admin/clients")
        retry_response = client.post(
            "/admin/clients/5/retry-provision",
            json={"password": "placeholder-pass"},
        )
        assign_response = client.post(
            "/admin/clients/5/assign-did", json={"did_number": "919484959244"}
        )

    assert list_response.status_code == 403
    assert retry_response.status_code == 403
    assert assign_response.status_code == 403


# ======== LIST ========


def test_list_clients_excludes_caller_and_reports_voicelink_state():
    app = _make_test_app()
    client = TestClient(app)

    org = _org()
    with patch("api.routes.admin_clients.db_client") as db:
        db.list_organizations_with_users = AsyncMock(return_value=[org])
        db.list_telephony_configurations_by_provider = AsyncMock(
            return_value=[
                SimpleNamespace(
                    is_default_outbound=True,
                    credentials={"did_number": "919484959244"},
                )
            ]
        )

        response = client.get("/admin/clients")

    assert response.status_code == 200
    db.list_organizations_with_users.assert_awaited_once_with(exclude_user_id=1)
    db.list_telephony_configurations_by_provider.assert_awaited_once_with(
        5, "voicelink"
    )

    [item] = response.json()["clients"]
    assert item["organization_id"] == 5
    assert item["organization_name"] == "org_oss_abc"
    assert item["owner_email"] == "jane@example.test"
    assert item["owner_provider_id"] == "oss_abc"
    assert item["voicelink_status"] == "pending"
    assert item["voicelink_username"] == "jane.5"
    assert item["voicelink_error"] == "No channels available"
    assert item["has_voicelink_config"] is True
    assert item["did_number"] == "919484959244"


def test_list_clients_handles_org_without_config_or_users():
    app = _make_test_app()
    client = TestClient(app)

    org = _org(users=[], voicelink_status=None, voicelink_error=None)
    with patch("api.routes.admin_clients.db_client") as db:
        db.list_organizations_with_users = AsyncMock(return_value=[org])
        db.list_telephony_configurations_by_provider = AsyncMock(return_value=[])

        response = client.get("/admin/clients")

    [item] = response.json()["clients"]
    assert item["owner_email"] is None
    assert item["has_voicelink_config"] is False
    assert item["did_number"] is None


# ======== RETRY PROVISION ========


def test_retry_provision_uses_stored_username_and_new_password():
    app = _make_test_app()
    client = TestClient(app)

    org = _org()
    provision_result = {
        "status": "provisioned",
        "client_id": "474",
        "username": "jane.5",
        "error": None,
    }
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_voicelink_clients_client",
            return_value=SimpleNamespace(is_configured=True),
        ),
        patch(
            "api.routes.admin_clients.provision_voicelink_client",
            new_callable=AsyncMock,
            return_value=provision_result,
        ) as provision,
    ):
        db.get_organization_with_users = AsyncMock(return_value=org)

        response = client.post(
            "/admin/clients/5/retry-provision",
            json={"password": "fresh-placeholder"},
        )

    assert response.status_code == 200
    provision.assert_awaited_once()
    assert provision.await_args.args == (5,)
    kwargs = provision.await_args.kwargs
    assert kwargs["email"] == "jane@example.test"
    assert kwargs["password"] == "fresh-placeholder"
    assert kwargs["username"] == "jane.5"

    body = response.json()
    assert body["voicelink_status"] == "provisioned"
    assert body["voicelink_client_id"] == "474"


def test_retry_provision_404_when_org_missing_and_503_when_creds_unset():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_with_users = AsyncMock(return_value=None)
        missing = client.post(
            "/admin/clients/999/retry-provision",
            json={"password": "fresh-placeholder"},
        )

    assert missing.status_code == 404

    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_voicelink_clients_client",
            return_value=SimpleNamespace(is_configured=False),
        ),
    ):
        db.get_organization_with_users = AsyncMock(return_value=_org())
        unconfigured = client.post(
            "/admin/clients/5/retry-provision",
            json={"password": "fresh-placeholder"},
        )

    assert unconfigured.status_code == 503


def test_retry_provision_rejects_short_passwords():
    app = _make_test_app()
    client = TestClient(app)

    response = client.post(
        "/admin/clients/5/retry-provision", json={"password": "short"}
    )

    assert response.status_code == 422


# ======== ASSIGN DID ========


def test_assign_did_creates_org_scoped_default_voicelink_config():
    app = _make_test_app()
    client = TestClient(app)

    org = _org(voicelink_client_id="474")
    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(return_value=org)
        db.list_telephony_configurations_by_provider = AsyncMock(return_value=[])
        db.create_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=77)
        )

        response = client.post(
            "/admin/clients/5/assign-did", json={"did_number": "919484959244"}
        )

    assert response.status_code == 200
    create_kwargs = db.create_telephony_configuration.await_args.kwargs
    assert create_kwargs["organization_id"] == 5
    assert create_kwargs["provider"] == "voicelink"
    assert create_kwargs["is_default_outbound"] is True
    assert create_kwargs["credentials"] == {
        "api_base": DEFAULT_API_BASE,
        "did_number": "919484959244",
        "username": "jane.5",
        "client_id": "474",
    }

    body = response.json()
    assert body["configuration_id"] == 77
    assert body["created"] is True
    assert body["did_number"] == "919484959244"
    assert body["client_id"] == "474"


def test_assign_did_updates_existing_config_and_marks_default():
    app = _make_test_app()
    client = TestClient(app)

    org = _org(voicelink_client_id=None)
    existing = SimpleNamespace(
        id=42,
        is_default_outbound=False,
        credentials={"api_base": "https://custom.example/api", "username": "u"},
    )
    updated = SimpleNamespace(id=42, is_default_outbound=False)
    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(return_value=org)
        db.list_telephony_configurations_by_provider = AsyncMock(
            return_value=[existing]
        )
        db.update_telephony_configuration = AsyncMock(return_value=updated)
        db.set_default_telephony_configuration = AsyncMock()

        response = client.post(
            "/admin/clients/5/assign-did",
            json={"did_number": "919484959244", "client_id": "888"},
        )

    assert response.status_code == 200
    update_call = db.update_telephony_configuration.await_args
    assert update_call.args == (42, 5)
    credentials = update_call.kwargs["credentials"]
    # Existing auth credentials are preserved; DID/client_id are stamped.
    assert credentials["username"] == "u"
    assert credentials["api_base"] == "https://custom.example/api"
    assert credentials["did_number"] == "919484959244"
    assert credentials["client_id"] == "888"
    db.set_default_telephony_configuration.assert_awaited_once_with(42, 5)

    body = response.json()
    assert body["created"] is False
    assert body["client_id"] == "888"


def test_assign_did_404_for_unknown_org():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(return_value=None)
        response = client.post(
            "/admin/clients/999/assign-did", json={"did_number": "919484959244"}
        )

    assert response.status_code == 404
