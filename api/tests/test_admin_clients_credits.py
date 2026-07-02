"""Tests for the admin Clients credits + on-demand KYC-status endpoints.

Same conventions as test_admin_clients_routes: a minimal FastAPI app with
the router mounted, ``get_superuser`` overridden for happy paths, and the
DB layer patched at the route module's ``db_client`` attribute.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.admin_clients import router
from api.services.auth.depends import get_superuser
from api.services.voicelink_kyc import VoiceLinkKycError


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
        "voicelink_status": "provisioned",
        "voicelink_client_id": "474",
        "voicelink_username": "jane.5",
        "voicelink_error": None,
        "voicelink_provision_secret": None,
        "free_call_seconds_remaining": 120,
        "users": [
            SimpleNamespace(id=9, provider_id="oss_abc", email="jane@example.test")
        ],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _kyc_client(**overrides):
    defaults = {"is_configured": True, "get_status": AsyncMock()}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ======== AUTHZ ========


def test_credits_and_kyc_endpoints_return_403_for_non_superuser():
    app = FastAPI()
    app.include_router(router)  # no overrides — real get_superuser runs
    client = TestClient(app)

    non_superuser = SimpleNamespace(id=2, is_superuser=False)
    with patch(
        "api.services.auth.depends.get_user",
        new=AsyncMock(return_value=non_superuser),
    ):
        grant_response = client.post(
            "/admin/clients/5/grant-credits", json={"minutes": 10}
        )
        kyc_response = client.get("/admin/clients/5/kyc-status")

    assert grant_response.status_code == 403
    assert kyc_response.status_code == 403


# ======== GRANT CREDITS ========


def test_grant_credits_adds_seconds_and_returns_new_balance():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(
            return_value=_org(free_call_seconds_remaining=120)
        )
        db.add_call_seconds = AsyncMock(return_value=720)

        response = client.post(
            "/admin/clients/5/grant-credits", json={"minutes": 10}
        )

    assert response.status_code == 200
    db.add_call_seconds.assert_awaited_once_with(5, 600)  # minutes → seconds
    body = response.json()
    assert body["organization_id"] == 5
    assert body["granted_seconds"] == 600
    assert body["credits_seconds_remaining"] == 720


def test_grant_credits_tops_up_depleted_zero_balance():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(
            return_value=_org(free_call_seconds_remaining=0)
        )
        db.add_call_seconds = AsyncMock(return_value=60)

        response = client.post(
            "/admin/clients/5/grant-credits", json={"minutes": 1}
        )

    assert response.status_code == 200
    assert response.json()["credits_seconds_remaining"] == 60


def test_grant_credits_409_for_unmetered_org():
    """NULL balance = unlimited; granting would silently meter the org."""
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(
            return_value=_org(free_call_seconds_remaining=None)
        )
        db.add_call_seconds = AsyncMock()

        response = client.post(
            "/admin/clients/5/grant-credits", json={"minutes": 10}
        )

    assert response.status_code == 409
    assert "unmetered" in response.json()["detail"]
    db.add_call_seconds.assert_not_awaited()


def test_grant_credits_404_for_unknown_org():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(return_value=None)

        response = client.post(
            "/admin/clients/999/grant-credits", json={"minutes": 10}
        )

    assert response.status_code == 404


def test_grant_credits_validates_minutes_bounds():
    app = _make_test_app()
    client = TestClient(app)

    for bad_body in ({"minutes": 0}, {"minutes": 100_001}, {}):
        response = client.post("/admin/clients/5/grant-credits", json=bad_body)
        assert response.status_code == 422, bad_body


# ======== CREDITS IN LIST ========


def test_list_clients_reports_credits_and_null_passthrough():
    app = _make_test_app()
    client = TestClient(app)

    metered = _org(id=5, provider_id="org_a", free_call_seconds_remaining=90)
    unmetered = _org(id=6, provider_id="org_b", free_call_seconds_remaining=None)
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_voicelink_clients_client",
            return_value=SimpleNamespace(is_configured=False),
        ),
    ):
        db.list_organizations_with_users = AsyncMock(
            return_value=[metered, unmetered]
        )
        db.list_telephony_configurations_by_provider = AsyncMock(return_value=[])

        response = client.get("/admin/clients")

    assert response.status_code == 200
    by_id = {c["organization_id"]: c for c in response.json()["clients"]}
    assert by_id[5]["credits_seconds_remaining"] == 90
    assert by_id[6]["credits_seconds_remaining"] is None  # unmetered → null


# ======== KYC STATUS (on demand, per org) ========


def test_kyc_status_disabled_when_reseller_unconfigured():
    app = _make_test_app()
    client = TestClient(app)

    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch(
            "api.routes.admin_clients.get_kyc_client",
            return_value=_kyc_client(is_configured=False),
        ),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())

        response = client.get("/admin/clients/5/kyc-status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "disabled"
    assert body["enabled"] is False


def test_kyc_status_no_client_when_unresolvable():
    """No resolvable client id → no upstream call (would hit reseller KYC)."""
    app = _make_test_app()
    client = TestClient(app)

    kyc = _kyc_client()
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch("api.routes.admin_clients.get_kyc_client", return_value=kyc),
        patch(
            "api.routes.admin_clients.resolve_org_voicelink_client_id",
            new=AsyncMock(return_value=(None, False)),
        ),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())

        response = client.get("/admin/clients/5/kyc-status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_client"
    assert body["enabled"] is True
    kyc.get_status.assert_not_awaited()


def test_kyc_status_ok_returns_self_serve_shape():
    app = _make_test_app()
    client = TestClient(app)

    envelope = {
        "status": True,
        "message": "ok",
        "data": {
            "kyc_status": "pending",
            "pan_verified": True,
            "aadhaar_verified": False,
            "gst_verified": None,
            "is_complete": False,
            "current_step": 3,
            "account_type": "individual",
        },
    }
    kyc = _kyc_client(get_status=AsyncMock(return_value=envelope))
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch("api.routes.admin_clients.get_kyc_client", return_value=kyc),
        patch(
            "api.routes.admin_clients.resolve_org_voicelink_client_id",
            new=AsyncMock(return_value=("474", True)),
        ) as resolve,
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())

        response = client.get("/admin/clients/5/kyc-status")

    assert response.status_code == 200
    resolve.assert_awaited_once_with(5)
    kyc.get_status.assert_awaited_once_with("474")
    body = response.json()
    assert body["status"] == "ok"
    assert body["enabled"] is True
    assert body["client_id_configured"] is True
    assert body["has_voicelink_config"] is True
    assert body["client_id"] == "474"
    assert body["kyc_status"] == "pending"
    assert body["pan_verified"] is True
    assert body["aadhaar_verified"] is False
    assert body["is_complete"] is False
    assert body["current_step"] == 3
    assert body["account_type"] == "individual"


def test_kyc_status_502_when_voicelink_fails():
    app = _make_test_app()
    client = TestClient(app)

    kyc = _kyc_client(get_status=AsyncMock(side_effect=VoiceLinkKycError("boom")))
    with (
        patch("api.routes.admin_clients.db_client") as db,
        patch("api.routes.admin_clients.get_kyc_client", return_value=kyc),
        patch(
            "api.routes.admin_clients.resolve_org_voicelink_client_id",
            new=AsyncMock(return_value=("474", True)),
        ),
    ):
        db.get_organization_by_id = AsyncMock(return_value=_org())

        response = client.get("/admin/clients/5/kyc-status")

    assert response.status_code == 502


def test_kyc_status_404_for_unknown_org():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.admin_clients.db_client") as db:
        db.get_organization_by_id = AsyncMock(return_value=None)

        response = client.get("/admin/clients/999/kyc-status")

    assert response.status_code == 404
