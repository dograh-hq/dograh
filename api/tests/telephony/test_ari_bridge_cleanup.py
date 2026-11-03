from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from api.services.telephony import ari_manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delete_status,get_status,error_expected",
    [
        (200, None, False),
        (204, None, False),
        (404, None, False),
        (409, 404, False),
        (409, 200, True),
        (409, 401, True),
        (409, 500, True),
        (409, aiohttp.ClientConnectionError("unreachable"), True),
        (409, TimeoutError("timed out"), True),
        (500, None, True),
    ],
)
async def test_bridge_cleanup_verifies_conflicts(
    monkeypatch, delete_status, get_status, error_expected
):
    session = MagicMock()
    session.__aenter__.return_value = session
    delete_response = AsyncMock(status=delete_status)
    delete_response.text.return_value = "Bridge not in Stasis application"
    session.delete.return_value.__aenter__.return_value = delete_response
    if isinstance(get_status, Exception):
        session.get.return_value.__aenter__.side_effect = get_status
    else:
        session.get.return_value.__aenter__.return_value = AsyncMock(status=get_status)
    monkeypatch.setattr(ari_manager.aiohttp, "ClientSession", lambda: session)
    log = MagicMock()
    monkeypatch.setattr(ari_manager, "logger", log)
    connection = ari_manager.ARIConnection(
        organization_id=1,
        telephony_configuration_id=10,
        ari_endpoint="http://asterisk.test:8088",
        app_name="dograh",
        app_password="secret",
        ws_client_name="dograh_ws",
    )

    await connection._delete_bridge("bridge-1")

    url = "http://asterisk.test:8088/ari/bridges/bridge-1"
    auth = aiohttp.BasicAuth("dograh", "secret")
    session.delete.assert_called_once_with(url, auth=auth)
    if delete_status == 409:
        session.get.assert_called_once_with(url, auth=auth)
    else:
        session.get.assert_not_called()
    assert log.error.called == error_expected
    if error_expected:
        assert str(delete_status) in log.error.call_args.args[0]
    if delete_status == 409 and get_status == 404:
        assert "already gone" in log.debug.call_args.args[0]
