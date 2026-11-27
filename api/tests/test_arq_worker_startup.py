"""The ARQ worker routes spans to each org's Langfuse project, as the API does."""

import pytest

from api.services.pipecat.tracing_config import handle_langfuse_sync
from api.services.worker_sync.protocol import WorkerSyncEventType
from api.tasks import arq as arq_worker


class SyncManager:
    def __init__(self, order, fail_start=False):
        self.handlers = {}
        self.order = order
        self.fail_start = fail_start
        self.stopped = False

    def register(self, event_type, handler):
        self.handlers[event_type] = handler

    async def start(self):
        self.order.append("subscribe")
        if self.fail_start:
            raise ConnectionError("redis unavailable")

    async def stop(self):
        self.stopped = True


def install(monkeypatch, *, load_error=None, fail_start=False):
    order = []

    async def load():
        order.append("load")
        if load_error:
            raise load_error

    manager = SyncManager(order, fail_start=fail_start)
    monkeypatch.setattr(arq_worker, "load_all_org_langfuse_credentials", load)
    monkeypatch.setattr(arq_worker, "WorkerSyncManager", lambda _url: manager)
    return order, manager


def test_worker_settings_install_lifecycle_hooks():
    assert arq_worker.WorkerSettings.on_startup is arq_worker.startup
    assert arq_worker.WorkerSettings.on_shutdown is arq_worker.shutdown


@pytest.mark.asyncio
async def test_startup_follows_changes_before_loading_org_exporters(monkeypatch):
    order, manager = install(monkeypatch)
    ctx = {}
    await arq_worker.startup(ctx)
    # Subscribed first, so a change committed during the load is still heard.
    assert order == ["subscribe", "load"]
    assert manager.handlers == {
        WorkerSyncEventType.LANGFUSE_CREDENTIALS: handle_langfuse_sync
    }
    await arq_worker.shutdown(ctx)
    assert manager.stopped


@pytest.mark.asyncio
async def test_tracing_failures_do_not_stop_the_worker(monkeypatch):
    order, manager = install(
        monkeypatch, load_error=RuntimeError("db unavailable"), fail_start=True
    )
    ctx = {}
    await arq_worker.startup(ctx)
    assert order == ["subscribe", "load"]
    assert "worker_sync_manager" not in ctx
    await arq_worker.shutdown(ctx)
    assert not manager.stopped
