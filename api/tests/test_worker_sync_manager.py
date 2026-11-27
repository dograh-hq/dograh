from unittest.mock import AsyncMock, Mock

import pytest

from api.services.worker_sync import manager as manager_module


@pytest.mark.asyncio
async def test_failed_start_leaves_no_connection_open(monkeypatch):
    pubsub = Mock(
        subscribe=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        close=AsyncMock(),
    )
    redis = Mock(pubsub=Mock(return_value=pubsub), close=AsyncMock())
    monkeypatch.setattr(
        manager_module.aioredis, "from_url", AsyncMock(return_value=redis)
    )
    sync_manager = manager_module.WorkerSyncManager("redis://example")

    with pytest.raises(ConnectionError):
        await sync_manager.start()

    pubsub.close.assert_awaited_once()
    redis.close.assert_awaited_once()
    # Nothing left for stop() to touch, and no listener running.
    await sync_manager.stop()
    pubsub.close.assert_awaited_once()
    redis.close.assert_awaited_once()
