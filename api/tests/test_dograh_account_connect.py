from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.errors.mps import MPSUnavailableError
from api.routes import dograh_account_connect as routes
from api.schemas.dograh_account_connect import AccountConnectPollRequest
from api.services import dograh_account_connect as connect

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
    user = SimpleNamespace(provider_id="u", selected_organization_id=ORG)
    with pytest.raises(HTTPException) as error:
        await routes.poll(AccountConnectPollRequest(device_code="device"), user=user)
    assert error.value.status_code == 404


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
