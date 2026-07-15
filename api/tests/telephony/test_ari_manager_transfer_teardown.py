import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.telephony import ari_manager
from api.services.telephony.ari_manager import ARIConnection


@pytest.fixture
def fake_call_concurrency(monkeypatch):
    fake = SimpleNamespace(unregister_active_call=AsyncMock(return_value=True))
    monkeypatch.setattr(ari_manager, "call_concurrency", fake)
    return fake


class _FakeDbClient:
    def __init__(self, gathered_context):
        self.workflow_run = SimpleNamespace(gathered_context=gathered_context)
        self.updated_contexts = []

    async def get_workflow_run_by_id(self, run_id: int):
        return self.workflow_run

    async def update_workflow_run(self, run_id: int, gathered_context: dict):
        self.updated_contexts.append((run_id, dict(gathered_context)))
        self.workflow_run.gathered_context = gathered_context


class _RecordingARIConnection(ARIConnection):
    def __init__(self):
        super().__init__(
            organization_id=1,
            telephony_configuration_id=10,
            ari_endpoint="http://asterisk.test:8088",
            app_name="dograh",
            app_password="secret",
            ws_client_name="dograh_ws",
        )
        self.deleted_bridges = []
        self.deleted_channels = []
        self.deleted_channel_runs = []
        self.deleted_ext_channels = []
        self.deleted_transfer_channel_mappings = []

    async def _delete_bridge(self, bridge_id: str):
        self.deleted_bridges.append(bridge_id)

    async def _delete_channel(self, channel_id: str):
        self.deleted_channels.append(channel_id)

    async def _delete_channel_run(self, *channel_ids: str):
        self.deleted_channel_runs.extend(channel_ids)

    async def _delete_ext_channel(self, channel_id: str | None):
        self.deleted_ext_channels.append(channel_id)

    async def _delete_transfer_channel_mapping(self, channel_id: str | None):
        self.deleted_transfer_channel_mappings.append(channel_id)

    async def _get_transfer_id_for_channel(self, channel_id: str):
        return None

    async def _handle_transfer_failed(
        self, transfer_id: str, channel_id: str, reason: str
    ):
        raise AssertionError(
            "completed transfer hangup should not publish transfer failure"
        )


def _completed_transfer_context():
    return {
        "call_id": "caller-chan",
        "ext_channel_id": "ext-chan",
        "bridge_id": "bridge-1",
        "transfer_state": "complete",
        "transfer_bridge_id": "bridge-1",
        "transfer_caller_channel_id": "caller-chan",
        "transfer_destination_channel_id": "dest-chan",
    }


@pytest.mark.asyncio
async def test_completed_transfer_tears_down_destination_when_caller_leaves(
    monkeypatch,
    fake_call_concurrency,
):
    fake_db = _FakeDbClient(_completed_transfer_context())
    monkeypatch.setattr(ari_manager, "db_client", fake_db)
    conn = _RecordingARIConnection()

    await conn._handle_stasis_end("caller-chan", "123")

    assert conn.deleted_bridges == ["bridge-1"]
    assert conn.deleted_channels == ["dest-chan"]
    assert "caller-chan" in conn.deleted_channel_runs
    assert "dest-chan" in conn.deleted_channel_runs
    assert conn.deleted_transfer_channel_mappings == ["dest-chan"]
    assert fake_db.workflow_run.gathered_context["transfer_state"] == "terminated"
    fake_call_concurrency.unregister_active_call.assert_awaited_with(123)


@pytest.mark.asyncio
async def test_completed_transfer_tears_down_caller_when_destination_leaves(
    monkeypatch,
    fake_call_concurrency,
):
    fake_db = _FakeDbClient(_completed_transfer_context())
    monkeypatch.setattr(ari_manager, "db_client", fake_db)
    conn = _RecordingARIConnection()

    await conn._handle_stasis_end("dest-chan", "123")

    assert conn.deleted_bridges == ["bridge-1"]
    assert conn.deleted_channels == ["caller-chan"]
    assert "caller-chan" in conn.deleted_channel_runs
    assert "dest-chan" in conn.deleted_channel_runs
    assert conn.deleted_transfer_channel_mappings == ["dest-chan"]
    assert fake_db.workflow_run.gathered_context["transfer_state"] == "terminated"
    fake_call_concurrency.unregister_active_call.assert_awaited_with(123)


@pytest.mark.asyncio
async def test_stasis_end_releases_concurrency_slot_on_normal_teardown(
    monkeypatch,
    fake_call_concurrency,
):
    """StasisEnd must release the org slot even when the pipeline never ran
    (e.g. the caller hung up before external media connected)."""
    fake_db = _FakeDbClient(
        {
            "call_id": "caller-chan",
            "ext_channel_id": "ext-chan",
            "bridge_id": "bridge-1",
        }
    )
    monkeypatch.setattr(ari_manager, "db_client", fake_db)
    conn = _RecordingARIConnection()

    await conn._handle_stasis_end("caller-chan", "123")

    fake_call_concurrency.unregister_active_call.assert_awaited_once_with(123)
    assert conn.deleted_bridges == ["bridge-1"]
    assert conn.deleted_channels == ["ext-chan"]


@pytest.mark.asyncio
async def test_completed_transfer_destination_destroyed_without_transfer_mapping_is_ignored():
    conn = _RecordingARIConnection()
    event = {
        "type": "ChannelDestroyed",
        "channel": {"id": "dest-chan", "state": "Up"},
        "cause": 16,
        "cause_txt": "Normal Clearing",
        "tech_cause": "unknown",
    }

    await conn._handle_event(json.dumps(event))
