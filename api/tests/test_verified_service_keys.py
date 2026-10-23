from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from api.routes import service_keys as key_routes
from api.services import mps_billing
from api.services import mps_service_key_client as client_module
from api.services.auth import depends
from api.services.mps_service_key_client import MPSServiceKeyClient

TEAM = "0f7e6977-e728-4368-9e5f-72c97b293cfc"
USER = "ba7b0490-17c7-4724-a358-76d1a42cd4a1"


@pytest.fixture
async def identity(monkeypatch):
    monkeypatch.setattr(depends, "AUTH_PROVIDER", "stack")
    monkeypatch.setattr(depends, "DEPLOYMENT_MODE", "saas")
    identity = {
        "id": USER,
        "primary_email": "user@example.com",
        "primary_email_verified": True,
        "selected_team_id": TEAM,
    }
    monkeypatch.setattr(depends.stackauth, "get_user", AsyncMock(return_value=identity))
    monkeypatch.setattr(
        depends.db_client,
        "get_organization_by_id",
        AsyncMock(return_value=SimpleNamespace(provider_id=TEAM)),
    )
    return identity


@pytest.mark.parametrize("verified", [True, False])
async def test_key_issuance_requires_current_verified_email(identity, verified):
    identity["primary_email_verified"] = verified
    user = SimpleNamespace(
        provider_id=USER, selected_organization_id=42, email="stale@example.com"
    )
    if verified:
        assert await depends.get_verified_stack_user(user, "Bearer token") is user
    else:
        with pytest.raises(HTTPException) as error:
            await depends.get_verified_stack_user(user, "Bearer token")
        assert error.value.status_code == 403


async def test_verified_identity_must_match_user_and_selected_team(identity):
    user = SimpleNamespace(provider_id=USER, selected_organization_id=42)
    identity["selected_team_id"] = "different-team"
    with pytest.raises(HTTPException) as error:
        await depends.get_verified_stack_user(user, "Bearer token")
    assert error.value.status_code == 403
    identity["id"] = "different-user"
    with pytest.raises(HTTPException) as error:
        await depends.get_verified_stack_user(user, "Bearer token")
    assert error.value.status_code == 401


async def test_oss_still_issues_trial_keys_without_claiming_a_tenant(monkeypatch):
    monkeypatch.setattr(client_module, "DEPLOYMENT_MODE", "oss")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"id": 1, "service_key": "oss_sk_trial"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handle), **kw),
    )
    response = await MPSServiceKeyClient().create_service_key(
        name="trial", created_by="oss_1_user", organization_id=42
    )
    assert response["service_key"] == "oss_sk_trial"
    import json

    payload = json.loads(requests[0].content)
    assert payload["created_by"] == "oss_1_user"
    assert "organization_id" not in payload and "stack_team_id" not in payload
    assert "X-Secret-Key" not in requests[0].headers


async def test_oss_key_route_keeps_local_auth_while_hosted_requires_verification(
    monkeypatch,
):
    user = SimpleNamespace(provider_id="oss_1_user", selected_organization_id=42)
    verified_user = AsyncMock(return_value=user)
    monkeypatch.setattr(depends, "get_verified_stack_user", verified_user)
    monkeypatch.setattr(depends, "DEPLOYMENT_MODE", "oss")
    assert await depends.get_service_key_user(user, "Bearer local-token") is user
    verified_user.assert_not_awaited()
    monkeypatch.setattr(depends, "DEPLOYMENT_MODE", "saas")
    assert await depends.get_service_key_user(user, "Bearer hosted-token") is user
    verified_user.assert_awaited_once_with(user, "Bearer hosted-token")


@pytest.mark.parametrize("operation", ["list", "archive"])
async def test_key_routes_preserve_missing_stack_team_error(monkeypatch, operation):
    monkeypatch.setattr(key_routes, "DEPLOYMENT_MODE", "saas")
    error = HTTPException(
        status_code=400, detail="Organization is missing a valid Stack team ID"
    )
    method = "get_service_keys" if operation == "list" else "archive_service_key"
    monkeypatch.setattr(
        key_routes.mps_service_key_client, method, AsyncMock(side_effect=error)
    )
    user = SimpleNamespace(provider_id=USER, selected_organization_id=42)
    with pytest.raises(HTTPException) as caught:
        if operation == "list":
            await key_routes.get_service_keys(user=user)
        else:
            await key_routes.archive_service_key("1", user=user)
    assert caught.value is error


async def test_signup_grant_retries_failure_and_caches_success(monkeypatch):
    monkeypatch.setattr(mps_billing, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(mps_billing, "_signup_grants", set())
    claim = AsyncMock(side_effect=[RuntimeError("temporary outage"), {"granted": True}])
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)
    for _ in range(3):
        await mps_billing.ensure_signup_grant(42, created_by=USER)
    assert claim.await_count == 2


async def test_oss_never_requests_signup_grants(monkeypatch):
    monkeypatch.setattr(mps_billing, "DEPLOYMENT_MODE", "oss")
    claim = AsyncMock()
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)
    await mps_billing.ensure_signup_grant(42, created_by="oss_1_user")
    claim.assert_not_awaited()


@pytest.mark.parametrize("mode", ["oss", "saas"])
async def test_transcription_uses_configured_key_and_server_correlation(
    monkeypatch, mode
):
    monkeypatch.setattr(client_module, "DEPLOYMENT_MODE", mode)
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("correlation-id/self"):
            return httpx.Response(200, json={"correlation_id": "server-correlation"})
        return httpx.Response(200, json={"transcript": "hello"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handle), **kw),
    )
    client = MPSServiceKeyClient()
    client._runtime_service_key = AsyncMock(return_value="mps_sk_user_configured")
    assert await client.transcribe_audio(b"audio", organization_id=42) == {
        "transcript": "hello"
    }
    client._runtime_service_key.assert_awaited_once_with(42)
    assert len(requests) == 2
    assert all(
        request.headers["Authorization"] == "Bearer mps_sk_user_configured"
        for request in requests
    )
    assert all(
        "X-Secret-Key" not in request.headers and "X-Created-By" not in request.headers
        for request in requests
    )
    assert b"server-correlation" in requests[1].content
    assert b'name="mps_billing_version"\r\n\r\n2' in requests[1].content


async def test_signup_grant_passes_trusted_team_and_actor(monkeypatch):
    monkeypatch.setattr(client_module, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(client_module, "DOGRAH_MPS_SECRET_KEY", "secret")
    monkeypatch.setattr(
        client_module.db_client,
        "get_organization_by_id",
        AsyncMock(return_value=SimpleNamespace(provider_id=TEAM)),
    )
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"granted": True})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handle), **kw),
    )
    assert await MPSServiceKeyClient().claim_signup_grant(
        organization_id=42, created_by=USER
    ) == {"granted": True}
    assert requests[0].url.path == f"/api/v1/billing/teams/{TEAM}/signup-grant"
    assert requests[0].headers["X-Secret-Key"] == "secret"
    assert requests[0].headers["X-Stack-Team-Id"] == TEAM
    assert requests[0].headers["X-Created-By"] == USER


@pytest.mark.parametrize(
    "verified,email",
    [(True, "user@example.com"), (False, "user@example.com"), (None, None)],
)
async def test_hosted_signup_grant_does_not_require_verified_email(
    monkeypatch, identity, verified, email
):
    identity["primary_email_verified"] = verified
    identity["primary_email"] = email
    user = SimpleNamespace(
        id=7, provider_id=USER, email="user@example.com", selected_organization_id=42
    )
    organization = SimpleNamespace(id=42, provider_id=TEAM)
    monkeypatch.setattr(
        depends.db_client,
        "get_or_create_user_by_provider_id",
        AsyncMock(return_value=(user, False)),
    )
    monkeypatch.setattr(
        depends.db_client,
        "get_or_create_organization_by_provider_id",
        AsyncMock(return_value=(organization, False)),
    )
    monkeypatch.setattr(depends, "ensure_organization_bootstrapped", AsyncMock())
    grant = AsyncMock()
    monkeypatch.setattr(depends, "ensure_signup_grant", grant)
    assert await depends.get_user(authorization="Bearer token") is user
    grant.assert_awaited_once_with(42, created_by=USER)
