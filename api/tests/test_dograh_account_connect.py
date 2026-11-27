import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException

from api.errors.mps import MPSUnavailableError
from api.routes import dograh_account_connect as routes
from api.schemas.dograh_account_connect import (
    AccountConnectPollRequest,
    AccountConnectStartRequest,
)
from api.services import dograh_account_connect as connect
from api.services.auth.depends import VerifiedStackIdentity
from api.services.mps_service_key_client import MPSServiceKeyClient

ORG = 42
KEY = "mps_sk_connected"


@pytest.fixture
def delivery(monkeypatch):
    receipt = {}
    result = {
        "status": "connected",
        "team_name": "Acme",
        "key_prefix": "mps_sk_con",
        "made_default": True,
    }

    async def store(organization_id, **kwargs):
        assert organization_id == ORG
        receipt[kwargs["receipt_key"]] = result
        return result

    db = SimpleNamespace(
        get_configuration_values=AsyncMock(side_effect=lambda *_: dict(receipt)),
        store_connected_service_key=AsyncMock(side_effect=store),
    )
    mps = SimpleNamespace(
        poll_account_connect=AsyncMock(
            return_value={
                "status": "approved",
                "service_key": KEY,
                "team_name": "Acme",
                "key_prefix": "mps_sk_con",
            }
        ),
        acknowledge_account_connect=AsyncMock(return_value={"status": "collected"}),
    )
    monkeypatch.setattr(connect, "db_client", db)
    monkeypatch.setattr(connect, "mps_service_key_client", mps)
    return SimpleNamespace(db=db, mps=mps, store=store)


async def test_poll_stores_the_key_before_acknowledging_and_never_returns_it(delivery):
    async def ack(_):
        delivery.db.store_connected_service_key.assert_awaited_once()

    delivery.mps.acknowledge_account_connect.side_effect = ack
    result = await connect.poll_account_connect(ORG, "device")
    assert result.status == "connected" and result.made_default
    assert KEY not in result.model_dump_json()
    assert (
        delivery.db.store_connected_service_key.await_args.kwargs["service_key"] == KEY
    )
    delivery.mps.acknowledge_account_connect.assert_awaited_once_with("device")


async def test_failed_local_save_does_not_acknowledge_and_can_retry(delivery):
    delivery.db.store_connected_service_key.side_effect = RuntimeError("DB unavailable")
    with pytest.raises(RuntimeError):
        await connect.poll_account_connect(ORG, "device")
    delivery.mps.acknowledge_account_connect.assert_not_awaited()
    delivery.db.store_connected_service_key.side_effect = delivery.store
    assert (await connect.poll_account_connect(ORG, "device")).status == "connected"
    delivery.mps.acknowledge_account_connect.assert_awaited_once()


async def test_failed_acknowledgement_retries_without_replacing_credentials(delivery):
    delivery.mps.acknowledge_account_connect.side_effect = [
        ConnectionError("lost ACK"),
        {"status": "collected"},
    ]
    with pytest.raises(ConnectionError):
        await connect.poll_account_connect(ORG, "device")
    assert (await connect.poll_account_connect(ORG, "device")).made_default
    delivery.db.store_connected_service_key.assert_awaited_once()
    delivery.mps.poll_account_connect.assert_awaited_once()
    assert delivery.mps.acknowledge_account_connect.await_count == 2


async def test_lost_browser_response_returns_durable_receipt(delivery):
    first = await connect.poll_account_connect(ORG, "device")
    again = await connect.poll_account_connect(ORG, "device")
    assert first == again
    delivery.db.store_connected_service_key.assert_awaited_once()
    delivery.mps.poll_account_connect.assert_awaited_once()


async def test_poll_passes_through_waiting_states(delivery):
    delivery.mps.poll_account_connect.return_value = {"status": "pending"}
    assert (await connect.poll_account_connect(ORG, "device")).status == "pending"
    delivery.db.store_connected_service_key.assert_not_awaited()
    delivery.mps.acknowledge_account_connect.assert_not_awaited()


def test_install_is_named_by_its_host():
    assert connect.install_name_for("http://127.0.0.1:3021/models") == "127.0.0.1:3021"
    assert connect.install_name_for(None) == "Self-hosted Dograh"


async def test_hosted_deployments_do_not_start_or_poll(monkeypatch):
    monkeypatch.setattr(routes, "DEPLOYMENT_MODE", "saas")
    mps = SimpleNamespace(
        start_account_connect=AsyncMock(), poll_account_connect=AsyncMock()
    )
    monkeypatch.setattr(connect, "mps_service_key_client", mps)
    user = SimpleNamespace(provider_id="u", selected_organization_id=ORG)
    with pytest.raises(HTTPException) as error:
        await routes.start(AccountConnectStartRequest(install_url=None), user=user)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        await routes.poll(AccountConnectPollRequest(device_code="device"), user=user)
    assert error.value.status_code == 404
    mps.start_account_connect.assert_not_awaited()
    mps.poll_account_connect.assert_not_awaited()


async def test_approve_names_the_team_stack_verified(monkeypatch):
    mps = SimpleNamespace(decide_account_connect_request=AsyncMock(return_value={}))
    monkeypatch.setattr(routes, "mps_service_key_client", mps)
    user = SimpleNamespace(provider_id="u", selected_organization_id=ORG)
    verified = VerifiedStackIdentity(
        user=user, identity={"selected_team": {"display_name": "Acme"}}
    )
    assert (await routes.approve("BCDF-GHJK", verified=verified)).status == "approved"
    mps.decide_account_connect_request.assert_awaited_once_with(
        "BCDF-GHJK",
        approve=True,
        organization_id=ORG,
        created_by="u",
        team_name="Acme",
    )


@pytest.fixture
def mps_http(monkeypatch):
    """A real MPS client whose requests go to queued responses."""
    requests, responses = [], []
    real_client = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = MPSServiceKeyClient()
    monkeypatch.setattr(
        client, "_tenant_headers", AsyncMock(return_value={"X-Tenant": "t"})
    )
    return SimpleNamespace(client=client, requests=requests, responses=responses)


async def test_connect_calls_return_the_mps_answer(mps_http):
    mps_http.responses.append(httpx.Response(200, json={"user_code": "BCDF-GHJK"}))
    assert await mps_http.client.start_account_connect(
        install_name="host", install_url=None, created_by="u"
    ) == {"user_code": "BCDF-GHJK"}
    [request] = mps_http.requests
    assert request.url.path.endswith("/api/v1/service-keys/connect/start")
    assert json.loads(request.content) == {
        "install_name": "host",
        "install_url": None,
        "created_by": "u",
    }


async def test_a_user_code_cannot_change_the_request_path(mps_http):
    mps_http.responses.append(httpx.Response(200, json={}))
    await mps_http.client.get_account_connect_request(
        "../approve", organization_id=ORG, created_by="u"
    )
    [request] = mps_http.requests
    assert request.url.raw_path.endswith(b"/connect/requests/..%2Fapprove")
    assert request.headers["X-Tenant"] == "t"


@pytest.mark.parametrize(
    "response,detail",
    [
        (httpx.Response(409, json={"detail": "This request was denied."}), None),
        (httpx.Response(409, json=["not", "an", "object"]), "Request failed"),
        (httpx.Response(409, text="not json"), "Request failed"),
    ],
)
async def test_mps_refusals_keep_their_status(mps_http, response, detail):
    mps_http.responses.append(response)
    with pytest.raises(HTTPException) as error:
        await mps_http.client.decide_account_connect_request(
            "BCDFGHJK", approve=False, organization_id=ORG, created_by="u"
        )
    assert error.value.status_code == 409
    assert error.value.detail == (detail or "This request was denied.")


@pytest.mark.parametrize(
    "response", [httpx.Response(502), httpx.ConnectError("connection refused")]
)
async def test_mps_outages_raise_unavailable(mps_http, response):
    mps_http.responses.append(response)
    with pytest.raises(MPSUnavailableError):
        await mps_http.client.poll_account_connect("device")


def _status_mps(monkeypatch, *, key, validate):
    mps = SimpleNamespace(
        find_runtime_service_key=AsyncMock(return_value=key),
        validate_service_key=validate,
    )
    monkeypatch.setattr(connect, "mps_service_key_client", mps)
    return mps


async def test_status_is_missing_without_a_dograh_key(monkeypatch):
    validate = Mock()
    mps = _status_mps(monkeypatch, key=None, validate=validate)
    assert (await connect.get_dograh_account_status(ORG)).status == "missing"
    mps.find_runtime_service_key.assert_awaited_once_with(ORG)
    validate.assert_not_called()


@pytest.mark.parametrize("accepted,status", [(True, "connected"), (False, "invalid")])
async def test_status_reports_whether_mps_accepts_the_key(
    monkeypatch, accepted, status
):
    validate = Mock(return_value=accepted)
    _status_mps(monkeypatch, key=KEY, validate=validate)
    assert (await connect.get_dograh_account_status(ORG)).status == status
    validate.assert_called_once_with(KEY, organization_id=ORG)


async def test_status_does_not_offer_reconnecting_during_an_mps_outage(monkeypatch):
    validate = Mock(side_effect=MPSUnavailableError("validate_service_key"))
    _status_mps(monkeypatch, key=KEY, validate=validate)
    assert (await connect.get_dograh_account_status(ORG)).status == "connected"


async def test_status_works_in_hosted_deployments(monkeypatch):
    monkeypatch.setattr(routes, "DEPLOYMENT_MODE", "saas")
    _status_mps(monkeypatch, key=KEY, validate=Mock(return_value=True))
    user = SimpleNamespace(provider_id="u", selected_organization_id=ORG)
    assert (await routes.get_status(user=user)).status == "connected"


async def test_email_verification_and_confirmation_require_local_request_owner(
    monkeypatch,
):
    monkeypatch.setattr(
        connect.db_client, "get_configuration_values", AsyncMock(return_value={})
    )
    call = AsyncMock()
    monkeypatch.setattr(connect.mps_service_key_client, "email_account_connect", call)
    with pytest.raises(HTTPException) as error:
        await connect.verify_email_connect(
            organization_id=ORG, user_id=1, device_code="foreign", code="123456"
        )
    assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        await connect.confirm_email_connect(
            organization_id=ORG, user_id=1, device_code="foreign", team_id="team"
        )
    call.assert_not_awaited()


async def test_email_start_records_owner_and_confirmation_stores_key(
    monkeypatch, delivery
):
    request = {
        "device_code": "device",
        "user_code": "BCDF-GHJK",
        "verification_uri": "https://app.dograh.com/connect",
        "verification_uri_complete": "https://app.dograh.com/connect?code=BCDF-GHJK",
        "expires_in": 600,
        "interval": 3,
    }
    owners = {}

    async def save_owner(org, key, value):
        assert org == ORG
        owners[key] = value

    monkeypatch.setattr(
        delivery.db,
        "upsert_configuration",
        AsyncMock(side_effect=save_owner),
        raising=False,
    )
    delivery.db.get_configuration_values.side_effect = lambda org, keys: {
        key: owners[key] for key in keys if key in owners
    }
    monkeypatch.setattr(
        delivery.mps,
        "email_account_connect",
        AsyncMock(return_value=request),
        raising=False,
    )
    await connect.start_email_connect(
        organization_id=ORG,
        user_id=9,
        email="person@example.com",
        install_url="http://localhost:3000",
        created_by="oss_user",
    )
    with pytest.raises(HTTPException):
        await connect.verify_email_connect(
            organization_id=ORG, user_id=10, device_code="device", code="123456"
        )
    result = await connect.confirm_email_connect(
        organization_id=ORG, user_id=9, device_code="device", team_id="team"
    )
    assert result.status == "connected"
    assert KEY not in result.model_dump_json()


async def test_email_mps_calls_have_no_cloud_credentials(mps_http):
    mps_http.responses.append(httpx.Response(200, json={"teams": []}))
    await mps_http.client.email_account_connect(
        "verify", {"device_code": "device", "code": "123456"}
    )
    request = mps_http.requests[0]
    assert request.url.path.endswith("/connect/email/verify")
    assert "X-Secret-Key" not in request.headers
    assert "X-Stack-Team-Id" not in request.headers


async def test_email_verification_preserves_stack_selected_team(monkeypatch):
    owner_key = connect._connect_owner_key("device")
    monkeypatch.setattr(
        connect.db_client,
        "get_configuration_values",
        AsyncMock(return_value={owner_key: {"user_id": 9}}),
    )
    teams = [{"id": "first", "name": "Personal"}, {"id": "selected", "name": "Company"}]
    monkeypatch.setattr(
        connect.mps_service_key_client,
        "email_account_connect",
        AsyncMock(return_value={"teams": teams, "selected_team": teams[1]}),
    )
    verified = await connect.verify_email_connect(
        organization_id=ORG, user_id=9, device_code="device", code="123456"
    )
    assert verified.selected_team.id == "selected"


async def test_oss_checkout_uses_installed_key_and_no_caller_selected_team(
    mps_http, monkeypatch
):
    monkeypatch.setattr(
        mps_http.client, "_runtime_service_key", AsyncMock(return_value=KEY)
    )
    mps_http.responses.append(
        httpx.Response(
            200, json={"checkout_url": "https://checkout.dograh.com/?session=short"}
        )
    )
    result = await mps_http.client.create_service_key_purchase_url(ORG)
    request = mps_http.requests[0]
    assert request.url.path.endswith("/billing/checkout-sessions/self")
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert "X-Secret-Key" not in request.headers
    assert "X-Stack-Team-Id" not in request.headers
    assert not request.content
    assert KEY not in result["checkout_url"]
