import asyncio
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


@pytest.fixture
def signup_state(monkeypatch):
    monkeypatch.setattr(mps_billing, "DEPLOYMENT_MODE", "saas")
    values = {}

    async def read(organization_id, keys):
        return {
            key: values[organization_id, key]
            for key in keys
            if (organization_id, key) in values
        }

    async def write(organization_id, key, value):
        values[organization_id, key] = value

    # Single-winner semantics of claim_configuration_lease, without staleness.
    async def claim_lease(organization_id, key, stale_after):
        if (organization_id, key) in values:
            return None
        token = f"token-{organization_id}-{key}"
        values[organization_id, key] = {"status": "pending", "owner_token": token}
        return token

    def held(organization_id, key, owner_token):
        value = values.get((organization_id, key))
        return isinstance(value, dict) and value.get("owner_token") == owner_token

    async def complete_lease(organization_id, key, owner_token):
        if held(organization_id, key, owner_token):
            values[organization_id, key] = {"status": "completed"}

    async def release_lease(organization_id, key, owner_token):
        if held(organization_id, key, owner_token):
            del values[organization_id, key]

    state = SimpleNamespace(
        values=values,
        read=AsyncMock(side_effect=read),
        write=AsyncMock(side_effect=write),
        claim_lease=AsyncMock(side_effect=claim_lease),
        release_lease=AsyncMock(side_effect=release_lease),
    )
    monkeypatch.setattr(mps_billing.db_client, "get_configuration_values", state.read)
    monkeypatch.setattr(mps_billing.db_client, "upsert_configuration", state.write)
    monkeypatch.setattr(
        mps_billing.db_client, "claim_configuration_lease", state.claim_lease
    )
    monkeypatch.setattr(
        mps_billing.db_client,
        "complete_configuration_lease",
        AsyncMock(side_effect=complete_lease),
    )
    monkeypatch.setattr(
        mps_billing.db_client, "release_configuration_lease", state.release_lease
    )
    return state


async def test_signup_grant_retries_failure_and_caches_success(
    monkeypatch, signup_state
):
    claim = AsyncMock(side_effect=[RuntimeError("temporary outage"), {"granted": True}])
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)
    for _ in range(3):
        await mps_billing.ensure_signup_grant(42, created_by=USER)
    assert claim.await_count == 2
    assert signup_state.write.await_count == 1


async def test_signup_grant_is_shared_by_members_but_scoped_to_organization(
    monkeypatch, signup_state
):
    claim = AsyncMock(return_value={"granted": True})
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)

    await mps_billing.ensure_signup_grant(42, created_by=USER)
    await mps_billing.ensure_signup_grant(42, created_by="another-member")
    claim.assert_awaited_once_with(organization_id=42, created_by=USER)

    await mps_billing.ensure_signup_grant(43, created_by="another-member")
    assert claim.await_count == 2
    claim.assert_awaited_with(organization_id=43, created_by="another-member")


@pytest.mark.parametrize("operation", ["read", "write"])
async def test_signup_grant_storage_failures_do_not_prevent_authentication(
    monkeypatch, signup_state, operation
):
    claim = AsyncMock(return_value={"granted": True})
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)
    storage = getattr(signup_state, operation)
    original = storage.side_effect
    storage.side_effect = RuntimeError("database unavailable")
    await mps_billing.ensure_signup_grant(42, created_by=USER)

    storage.side_effect = original
    await mps_billing.ensure_signup_grant(42, created_by=USER)
    await mps_billing.ensure_signup_grant(42, created_by="another-member")
    assert claim.await_count == (1 if operation == "read" else 2)


async def test_denied_signup_claim_does_not_block_an_eligible_teammate(
    monkeypatch, signup_state
):
    claim = AsyncMock(side_effect=[{"granted": False}, {"granted": True}])
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)

    # This person may already have received their one-time grant in another team.
    await mps_billing.ensure_signup_grant(42, created_by=USER)
    await mps_billing.ensure_signup_grant(42, created_by=USER)
    claim.assert_awaited_once_with(organization_id=42, created_by=USER)

    await mps_billing.ensure_signup_grant(42, created_by="eligible-member")
    claim.assert_awaited_with(organization_id=42, created_by="eligible-member")
    await mps_billing.ensure_signup_grant(42, created_by="third-member")
    assert claim.await_count == 2


async def test_concurrent_first_requests_claim_the_signup_grant_once(
    monkeypatch, signup_state
):
    release = asyncio.Event()

    async def slow_claim(**kwargs):
        await release.wait()
        return {"granted": False}

    claim = AsyncMock(side_effect=slow_claim)
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)

    # A first page load authenticates several requests at once.
    requests = [
        asyncio.create_task(mps_billing.ensure_signup_grant(42, created_by=USER))
        for _ in range(4)
    ]
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*requests)

    claim.assert_awaited_once_with(organization_id=42, created_by=USER)
    await mps_billing.ensure_signup_grant(42, created_by=USER)
    assert claim.await_count == 1


async def test_signup_claims_recorded_before_the_lease_are_still_done(
    monkeypatch, signup_state
):
    claim = AsyncMock(return_value={"granted": True})
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)
    signup_state.values[42, f"MPS_SIGNUP_GRANT:{USER}"] = True

    await mps_billing.ensure_signup_grant(42, created_by=USER)
    claim.assert_not_awaited()
    signup_state.claim_lease.assert_not_awaited()


async def test_failed_signup_claim_releases_the_lease_for_the_next_request(
    monkeypatch, signup_state
):
    claim = AsyncMock(side_effect=[RuntimeError("temporary outage"), {"granted": True}])
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)

    await mps_billing.ensure_signup_grant(42, created_by=USER)
    signup_state.release_lease.assert_awaited_once()
    assert (42, f"MPS_SIGNUP_GRANT:{USER}") not in signup_state.values

    await mps_billing.ensure_signup_grant(42, created_by=USER)
    assert claim.await_count == 2
    assert signup_state.values[42, "MPS_SIGNUP_GRANT"] is True


async def test_oss_never_requests_signup_grants(monkeypatch, signup_state):
    monkeypatch.setattr(mps_billing, "DEPLOYMENT_MODE", "oss")
    claim = AsyncMock()
    monkeypatch.setattr(mps_billing.mps_service_key_client, "claim_signup_grant", claim)
    await mps_billing.ensure_signup_grant(42, created_by="oss_1_user")
    claim.assert_not_awaited()
    signup_state.read.assert_not_awaited()
    signup_state.write.assert_not_awaited()


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
