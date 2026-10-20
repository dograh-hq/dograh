from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
import redis.asyncio as aioredis

import api.db
from api.services.telephony import call_transfer_manager
from api.services.telephony.providers.ari.strategies import ARIBridgeSwapStrategy


@pytest.mark.asyncio
async def test_bridge_swap_preserves_context_saved_after_its_read(monkeypatch):
    snapshot = {
        "bridge_id": "bridge-1",
        "ext_channel_id": "ext-chan",
        "name": "old",
    }
    stored_context = {**snapshot, "name": "Ada"}

    async def save_patch(*, run_id, gathered_context):
        stored_context.update(gathered_context)

    monkeypatch.setattr(
        api.db,
        "db_client",
        SimpleNamespace(
            get_workflow_run_by_id=AsyncMock(
                return_value=SimpleNamespace(gathered_context=snapshot)
            ),
            update_workflow_run=AsyncMock(side_effect=save_patch),
        ),
    )
    manager = SimpleNamespace(
        find_transfer_context_for_call=AsyncMock(
            return_value=SimpleNamespace(transfer_id="transfer-1", call_sid="dest-chan")
        ),
        remove_transfer_context=AsyncMock(),
    )
    monkeypatch.setattr(
        call_transfer_manager,
        "get_call_transfer_manager",
        AsyncMock(return_value=manager),
    )
    redis_client = AsyncMock()
    redis_client.get.return_value = "123"
    monkeypatch.setattr(aioredis, "from_url", lambda *_args, **_kwargs: redis_client)
    session = MagicMock()
    session.__aenter__.return_value = session
    for request in (session.post, session.delete):
        request.return_value.__aenter__.return_value = SimpleNamespace(status=204)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: session)

    result = await ARIBridgeSwapStrategy().execute_transfer(
        {
            "channel_id": "caller-chan",
            "ari_endpoint": "http://asterisk.test:8088",
            "app_name": "dograh",
            "app_password": "secret",
        }
    )

    assert result is True
    assert stored_context["name"] == "Ada"
    assert stored_context["transfer_state"] == "in-progress"
    assert stored_context["transfer_bridge_id"] == "bridge-1"
    assert stored_context["transfer_caller_channel_id"] == "caller-chan"
    assert stored_context["transfer_destination_channel_id"] == "dest-chan"
    manager.remove_transfer_context.assert_awaited_once_with("transfer-1")
