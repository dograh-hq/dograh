from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.providers.plivo.strategies import (
    PlivoConferenceStrategy,
    PlivoHangupStrategy,
)


class _Response:
    def __init__(self, status: int, body: str = ""):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self):
        return self._body


class _Session:
    def __init__(self, *, post_status: int = 202, delete_status: int = 204):
        self.post_status = post_status
        self.delete_status = delete_status
        self.post_calls = []
        self.delete_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def post(self, endpoint, *, json, auth):
        self.post_calls.append((endpoint, json, auth))
        return _Response(self.post_status)

    def delete(self, endpoint, *, auth):
        self.delete_calls.append((endpoint, auth))
        return _Response(self.delete_status)


@pytest.mark.asyncio
async def test_transfer_strategy_redirects_original_call_to_conference_xml():
    transfer_context = SimpleNamespace(
        transfer_id="transfer-1",
        original_call_sid="original-call-uuid",
        conference_name="conference-1",
    )
    manager = SimpleNamespace(
        find_transfer_context_for_call=AsyncMock(return_value=transfer_context),
        remove_transfer_context=AsyncMock(),
    )
    session = _Session()

    with (
        patch(
            "api.services.telephony.providers.plivo.strategies.get_call_transfer_manager",
            new=AsyncMock(return_value=manager),
        ),
        patch(
            "api.services.telephony.providers.plivo.strategies.get_backend_endpoints",
            new=AsyncMock(return_value=("https://backend.test", "wss://backend.test")),
        ),
        patch(
            "api.services.telephony.providers.plivo.strategies.aiohttp.ClientSession",
            return_value=session,
        ),
    ):
        result = await PlivoConferenceStrategy().execute_transfer(
            {
                "call_id": "original-call-uuid",
                "auth_id": "MA123",
                "auth_token": "secret",
            }
        )

    assert result is True
    manager.find_transfer_context_for_call.assert_awaited_once_with(
        "original-call-uuid"
    )
    assert session.post_calls[0][0].endswith("/Account/MA123/Call/original-call-uuid/")
    assert session.post_calls[0][1] == {
        "legs": "aleg",
        "aleg_url": "https://backend.test/api/v1/telephony/plivo/transfer-xml/conference-1/transfer-1?leg=aleg",
        "aleg_method": "POST",
    }
    manager.remove_transfer_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_strategy_cleans_up_when_plivo_rejects_redirect():
    transfer_context = SimpleNamespace(
        transfer_id="transfer-1",
        original_call_sid="original-call-uuid",
        conference_name="conference-1",
    )
    manager = SimpleNamespace(
        find_transfer_context_for_call=AsyncMock(return_value=transfer_context),
        remove_transfer_context=AsyncMock(),
    )

    with (
        patch(
            "api.services.telephony.providers.plivo.strategies.get_call_transfer_manager",
            new=AsyncMock(return_value=manager),
        ),
        patch(
            "api.services.telephony.providers.plivo.strategies.get_backend_endpoints",
            new=AsyncMock(return_value=("https://backend.test", "wss://backend.test")),
        ),
        patch(
            "api.services.telephony.providers.plivo.strategies.aiohttp.ClientSession",
            return_value=_Session(post_status=400),
        ),
    ):
        result = await PlivoConferenceStrategy().execute_transfer(
            {
                "call_id": "original-call-uuid",
                "auth_id": "MA123",
                "auth_token": "secret",
            }
        )

    assert result is False
    manager.remove_transfer_context.assert_awaited_once_with("transfer-1")


@pytest.mark.asyncio
async def test_hangup_strategy_treats_already_ended_call_as_success():
    session = _Session(delete_status=404)
    with patch(
        "api.services.telephony.providers.plivo.strategies.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await PlivoHangupStrategy().execute_hangup(
            {
                "call_id": "call-uuid",
                "auth_id": "MA123",
                "auth_token": "secret",
            }
        )

    assert result is True
    assert session.delete_calls[0][0].endswith("/Account/MA123/Call/call-uuid/")
