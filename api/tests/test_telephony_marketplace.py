"""Telephony marketplace service: available filtering, assign, org-number lookup."""

from unittest.mock import AsyncMock, patch

from api.services import telephony_marketplace as mkt


def _fake_client(available=None, clients=None, configured=True):
    c = AsyncMock()
    c.is_configured = configured
    c.available_dids = AsyncMock(return_value=available or [])
    c.list_clients = AsyncMock(return_value=clients or [])
    c.map_did = AsyncMock(return_value={"status": True})
    return c


def _patch(fc):
    return patch.object(mkt, "get_voicelink_clients_client", return_value=fc)


async def test_list_available_keeps_only_status_1():
    fc = _fake_client(
        available=[
            {"did_id": 1, "did_number": "9111", "user_status": 1},
            {"did_id": 2, "did_number": "9222", "user_status": 2},  # Assigned
        ]
    )
    with _patch(fc):
        nums = await mkt.list_available_numbers()
    assert [n["did_id"] for n in nums] == [1]


async def test_list_available_empty_when_unconfigured():
    with _patch(_fake_client(configured=False)):
        assert await mkt.list_available_numbers() == []


async def test_assign_number_calls_map_did():
    fc = _fake_client()
    with _patch(fc):
        await mkt.assign_number("474", 942)
    fc.map_did.assert_awaited_once()
    payload = fc.map_did.await_args.args[0]
    assert payload["client_id"] == "474"
    assert payload["did_id"] == 942
    assert payload["user_status"] == 2


async def test_list_org_numbers_filters_to_client():
    fc = _fake_client(
        clients=[
            {"id": 474, "dids": [{"did_id": 942, "did_number": "9484959244"}]},
            {"id": 1333, "dids": []},
        ]
    )
    with _patch(fc):
        nums = await mkt.list_org_numbers("474")
    assert [n["did_id"] for n in nums] == [942]


async def test_list_org_numbers_none_client():
    with _patch(_fake_client()):
        assert await mkt.list_org_numbers(None) == []
