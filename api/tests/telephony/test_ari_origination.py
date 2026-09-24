"""Outbound ARI events can arrive before the originate HTTP response."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientConnectionError
from fastapi import HTTPException

from api.enums import TelephonyCallStatus
from api.services.telephony import ari_manager
from api.services.telephony.providers.ari import channel_registry
from api.services.telephony.providers.ari import provider as provider_module

RUN_ID = 123


@pytest.fixture
def ari_call(monkeypatch):
    mappings = {}

    async def set_mapping(key, value, *, ex):
        assert ex == channel_registry.CHANNEL_KEY_TTL
        mappings[key] = value

    async def delete_mapping(*keys):
        for key in keys:
            mappings.pop(key, None)

    redis = SimpleNamespace(
        set=AsyncMock(side_effect=set_mapping),
        get=AsyncMock(side_effect=mappings.get),
        delete=AsyncMock(side_effect=delete_mapping),
    )
    monkeypatch.setattr(channel_registry, "_redis_client", redis)
    provider = provider_module.ARIProvider(
        {
            "ari_endpoint": "http://asterisk.test:8088",
            "app_name": "dograh",
            "app_password": "secret",
            "stasis_app_name": "dograh-config-10",
        }
    )
    session = MagicMock()
    session.__aenter__.return_value = session
    monkeypatch.setattr(
        provider_module.aiohttp, "ClientSession", MagicMock(return_value=session)
    )
    return SimpleNamespace(
        provider=provider, session=session, redis=redis, mappings=mappings
    )


@pytest.mark.asyncio
async def test_destroy_before_originate_response_reaches_terminal_processing(
    ari_call, monkeypatch
):
    connection = ari_manager.ARIConnection(
        organization_id=1,
        telephony_configuration_id=10,
        ari_endpoint="http://asterisk.test:8088",
        app_name="dograh",
        app_password="secret",
    )
    connection._redis_client = ari_call.redis
    monkeypatch.setattr(
        connection, "_get_transfer_id_for_channel", AsyncMock(return_value=None)
    )
    run = SimpleNamespace(
        mode="ari",
        call_type="outbound",
        initial_context={"telephony_configuration_id": 10},
        gathered_context={},
        state="initialized",
        is_completed=False,
    )
    monkeypatch.setattr(
        ari_manager,
        "db_client",
        SimpleNamespace(get_workflow_run=AsyncMock(return_value=run)),
    )
    process_status = AsyncMock()
    monkeypatch.setattr(ari_manager, "_process_status_update", process_status)
    destroyed = asyncio.Event()
    release = connection._release_destroyed_channel

    async def handle_destroy(*args, **kwargs):
        try:
            await release(*args, **kwargs)
        finally:
            destroyed.set()

    monkeypatch.setattr(connection, "_release_destroyed_channel", handle_destroy)

    @asynccontextmanager
    async def originate(url, *, params, auth):
        channel_id = params.get("channelId", "asterisk-assigned-id")
        await connection._handle_event(
            json.dumps(
                {
                    "type": "ChannelDestroyed",
                    "channel": {"id": channel_id},
                    "cause": 17,
                    "cause_txt": "User busy",
                }
            )
        )
        await asyncio.wait_for(destroyed.wait(), timeout=2)
        yield SimpleNamespace(
            status=200,
            text=AsyncMock(
                return_value=json.dumps({"id": channel_id, "state": "Down"})
            ),
        )

    ari_call.session.post.side_effect = originate
    result = await ari_call.provider.initiate_call(
        "1001", "", workflow_run_id=RUN_ID, workflow_id=42
    )

    process_status.assert_awaited_once()
    run_id, status = process_status.call_args.args
    assert run_id == RUN_ID
    assert status.call_id == result.call_id
    assert status.status == TelephonyCallStatus.BUSY
    assert result.provider_metadata["call_id"] == result.call_id
    # The HTTP response must not recreate a mapping consumed by destruction.
    assert ari_call.mappings == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_run_id", [RUN_ID, None])
async def test_origination_uses_a_unique_registered_id(ari_call, workflow_run_id):
    @asynccontextmanager
    async def originate(url, *, params, auth):
        assert url == "http://asterisk.test:8088/ari/channels"
        assert params["endpoint"] == "PJSIP/1001"
        assert params["app"] == "dograh-config-10"
        assert params["callerId"] == "1002"
        channel_id = params["channelId"]
        key = f"{channel_registry.CHANNEL_KEY_PREFIX}{channel_id}"
        if workflow_run_id is not None:
            assert ari_call.mappings[key] == str(workflow_run_id)
        else:
            assert key not in ari_call.mappings
        yield SimpleNamespace(
            status=200,
            text=AsyncMock(
                return_value=json.dumps(
                    {"id": channel_id, "state": "Down", "name": "PJSIP/1001-1"}
                )
            ),
        )

    ari_call.session.post.side_effect = originate
    results = [
        await ari_call.provider.initiate_call(
            "1001", "", workflow_run_id=workflow_run_id, from_number="1002"
        )
        for _ in range(2)
    ]

    assert results[0].call_id != results[1].call_id
    for result in results:
        assert result.call_id == result.raw_response["id"]
        assert result.provider_metadata == {
            "call_id": result.call_id,
            "channel_name": "PJSIP/1001-1",
        }
        assert result.caller_number == "1002"
    assert len(ari_call.mappings) == (2 if workflow_run_id is not None else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 500])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_rejected_origination_removes_mapping_without_masking_error(
    ari_call, status, cleanup_fails
):
    if cleanup_fails:
        ari_call.redis.delete.side_effect = RuntimeError("Redis unavailable")

    @asynccontextmanager
    async def originate(url, *, params, auth):
        key = f"{channel_registry.CHANNEL_KEY_PREFIX}{params['channelId']}"
        assert ari_call.mappings[key] == str(RUN_ID)
        yield SimpleNamespace(status=status, text=AsyncMock(return_value="Rejected"))

    ari_call.session.post.side_effect = originate
    with pytest.raises(HTTPException) as exc:
        await ari_call.provider.initiate_call("1001", "", workflow_run_id=RUN_ID)

    assert exc.value.status_code == status
    assert exc.value.detail == "Failed to create ARI channel: Rejected"
    ari_call.redis.delete.assert_awaited_once()
    assert bool(ari_call.mappings) is cleanup_fails


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TimeoutError, ClientConnectionError, asyncio.CancelledError]
)
async def test_uncertain_origination_keeps_mapping_for_later_events(ari_call, error):
    ari_call.session.post.side_effect = error("Response lost")

    with pytest.raises(error, match="Response lost"):
        await ari_call.provider.initiate_call("1001", "", workflow_run_id=RUN_ID)

    assert list(ari_call.mappings.values()) == [str(RUN_ID)]
    ari_call.redis.delete.assert_not_awaited()
