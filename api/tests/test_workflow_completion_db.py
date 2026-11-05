import asyncio
import importlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from api.db.models import WorkflowModel
from api.services.telephony import ari_manager, external_pbx_writeback
from api.services.telephony.providers.ari.external_pbx import create_adapter
from api.tasks import workflow_completion
from api.tests.test_run_model_snapshot_db import snapshot_db as snapshot_db_fixture

completion_db = snapshot_db_fixture


@pytest.mark.asyncio
async def test_concurrent_completions_preserve_all_disposition_codes(completion_db):
    client, _, run_id = completion_db
    run = await client.get_workflow_run_by_id(run_id)
    codes = [f"outcome-{index}" for index in range(8)]
    await asyncio.wait_for(
        asyncio.gather(
            *[
                client.add_call_disposition_code(run.workflow_id, code)
                for code in [*codes, *codes]
            ]
        ),
        timeout=10,
    )
    async with client.async_session() as session:
        workflow = await session.get(WorkflowModel, run.workflow_id)
        stored = workflow.call_disposition_codes["disposition_codes"]
    assert sorted(stored) == sorted(codes)


async def prepare(client, run_id):
    await client.update_workflow_run(
        run_id,
        is_completed=True,
        gathered_context={
            "call_status": "end_call",
            "call_disposition": "end_call",
            "mapped_call_disposition": "end_call",
            "call_tags": ["user_speech"],
            "extracted_variables": {"earlier": "kept"},
        },
        logs={"conversation": []},
        extra={
            "workflow_completion": {
                "status": "pending",
                "created_at": time.time() - 3600,
                "final_extraction": {"status": "pending"},
            }
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age_seconds", "eligible"), [(1200, False), (1800, False), (1801, True)]
)
async def test_recovery_waits_for_completion_to_be_older_than_30_minutes(
    completion_db, monkeypatch, age_seconds, eligible
):
    client, _, run_id = completion_db
    now = time.time()
    monkeypatch.setattr(
        "api.db.workflow_completion_client.time", SimpleNamespace(time=lambda: now)
    )
    await client.update_workflow_run(
        run_id,
        is_completed=True,
        extra={
            "workflow_completion": {
                "status": "pending",
                "created_at": now - age_seconds,
            }
        },
    )
    assert (run_id in await client.get_overdue_workflow_completions()) is eligible


@pytest.mark.asyncio
async def test_completion_claim_is_exclusive_and_scoped(completion_db):
    client, organization_id, run_id = completion_db
    await prepare(client, run_id)
    assert (
        await client.claim_workflow_completion(
            run_id, organization_id + 1000000, "foreign"
        )
        is None
    )
    claims = await asyncio.gather(
        *(
            client.claim_workflow_completion(run_id, organization_id, f"owner-{index}")
            for index in range(4)
        )
    )
    assert sum(claim is not None for claim in claims) == 1
    assert run_id not in await client.get_overdue_workflow_completions()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["done", "failed"])
async def test_extraction_and_context_patch_preserve_each_others_fields(
    completion_db, status
):
    client, organization_id, run_id = completion_db
    await prepare(client, run_id)
    await client.claim_workflow_completion(run_id, organization_id, "owner")
    saved, _ = await asyncio.gather(
        client.finish_final_extraction(
            run_id,
            organization_id,
            "owner",
            variables={"name": "Ada", "tag_lead": "qualified"},
            disposition="qualified",
            fallback="end_call",
            mapped_disposition="QUAL",
            status=status,
            error_class="TimeoutError" if status == "failed" else None,
        ),
        client.update_workflow_run(
            run_id,
            gathered_context={
                "call_tags": ["end_call"],
                "transfer_state": "terminated",
            },
        ),
    )
    assert saved
    await client.update_workflow_run(
        run_id,
        extra={
            "workflow_completion": {
                "status": "pending",
                "final_extraction": {"status": "pending"},
            },
            "unrelated": "kept",
        },
    )
    run = await client.get_workflow_run_by_id(run_id)
    completion = run.extra["workflow_completion"]
    assert completion["token"] == "owner"
    assert completion["final_extraction"]["status"] == status
    assert run.extra["unrelated"] == "kept"
    assert "final_extraction" not in run.gathered_context
    assert run.gathered_context["name"] == "Ada"
    assert run.gathered_context["mapped_call_disposition"] == "QUAL"
    assert run.gathered_context["extracted_variables"] == {
        "earlier": "kept",
        "name": "Ada",
        "tag_lead": "qualified",
    }
    assert set(run.gathered_context["call_tags"]) == {
        "user_speech",
        "qualified",
        "end_call",
    }
    assert run.gathered_context["transfer_state"] == "terminated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transfer_state,channel_id,expected_state",
    [
        ("in-progress", "ext-chan", "complete"),
        ("complete", "caller-chan", "terminated"),
        ("complete", "dest-chan", "terminated"),
    ],
)
async def test_transfer_cleanup_preserves_extraction_committed_after_its_read(
    completion_db, monkeypatch, transfer_state, channel_id, expected_state
):
    client, organization_id, run_id = completion_db
    await prepare(client, run_id)
    await client.update_workflow_run(
        run_id,
        gathered_context={
            "call_id": "caller-chan",
            "ext_channel_id": "ext-chan",
            "bridge_id": "bridge-1",
            "transfer_state": transfer_state,
            "transfer_destination_channel_id": "dest-chan",
            "name": "old",
        },
    )
    await client.claim_workflow_completion(run_id, organization_id, "owner")

    async def read_before_extraction_commit(run_id):
        snapshot = await client.get_workflow_run_by_id(run_id)
        assert await client.finish_final_extraction(
            run_id,
            organization_id,
            "owner",
            variables={"name": "Ada"},
            disposition="qualified",
            fallback="end_call",
            mapped_disposition="QUAL",
            status="done",
            error_class=None,
        )
        # The handler resumes with the snapshot it read before extraction committed.
        assert snapshot.gathered_context["name"] == "old"
        return snapshot

    monkeypatch.setattr(
        ari_manager,
        "db_client",
        SimpleNamespace(
            get_workflow_run_by_id=read_before_extraction_commit,
            update_workflow_run=client.update_workflow_run,
        ),
    )
    monkeypatch.setattr(
        ari_manager.call_concurrency, "unregister_active_call", AsyncMock()
    )
    connection = ari_manager.ARIConnection(
        organization_id=organization_id,
        telephony_configuration_id=10,
        ari_endpoint="http://asterisk.test:8088",
        app_name="dograh",
        app_password="secret",
        ws_client_name="dograh_ws",
    )
    for method in (
        "_delete_bridge",
        "_delete_channel",
        "_delete_channel_run",
        "_delete_ext_channel",
        "_delete_transfer_channel_mapping",
    ):
        monkeypatch.setattr(connection, method, AsyncMock())

    await connection._handle_stasis_end(channel_id, str(run_id))

    run = await client.get_workflow_run_by_id(run_id)
    assert run.gathered_context["transfer_state"] == expected_state
    assert run.gathered_context["name"] == "Ada"
    assert run.gathered_context["extracted_variables"] == {
        "earlier": "kept",
        "name": "Ada",
    }
    assert run.gathered_context["call_disposition"] == "qualified"
    assert run.gathered_context["mapped_call_disposition"] == "QUAL"


@pytest.mark.asyncio
async def test_user_variable_is_separate_from_extraction_metadata(completion_db):
    client, organization_id, run_id = completion_db
    await prepare(client, run_id)
    await client.update_workflow_run(
        run_id, gathered_context={"final_extraction": {"status": "done"}}
    )
    await client.claim_workflow_completion(run_id, organization_id, "owner")
    assert await client.finish_final_extraction(
        run_id,
        organization_id,
        "owner",
        variables={"final_extraction": "user value"},
        disposition=None,
        fallback="end_call",
        mapped_disposition=None,
        status="done",
        error_class=None,
    )
    run = await client.get_workflow_run_by_id(run_id)
    assert run.gathered_context["final_extraction"] == "user value"
    assert run.extra["workflow_completion"]["final_extraction"]["status"] == "done"
    # Explicit patches remain allowed after extraction; metadata does not freeze values.
    await client.update_workflow_run(
        run_id, gathered_context={"final_extraction": "updated user value"}
    )
    run = await client.get_workflow_run_by_id(run_id)
    assert run.gathered_context["final_extraction"] == "updated user value"
    assert run.extra["workflow_completion"]["final_extraction"]["status"] == "done"


@pytest.mark.asyncio
async def test_expired_worker_cannot_commit_after_recovery(completion_db):
    client, organization_id, run_id = completion_db
    await prepare(client, run_id)
    state = await client.claim_workflow_completion(run_id, organization_id, "old")
    state["lease_until"] = time.time() - 1
    await client.update_workflow_run(run_id, extra={"workflow_completion": state})
    assert run_id in await client.get_overdue_workflow_completions()
    assert await client.claim_workflow_completion(run_id, organization_id, "new")
    assert not await client.finish_final_extraction(
        run_id,
        organization_id,
        "old",
        variables={"stale": True},
        disposition=None,
        fallback="end_call",
        mapped_disposition=None,
        status="done",
        error_class=None,
    )


@pytest.mark.asyncio
async def test_recovery_finds_runs_after_extraction_but_before_integrations(
    completion_db,
):
    client, organization_id, run_id = completion_db
    await prepare(client, run_id)
    state = await client.claim_workflow_completion(run_id, organization_id, "old")
    await client.finish_final_extraction(
        run_id,
        organization_id,
        "old",
        variables={},
        disposition=None,
        fallback="end_call",
        mapped_disposition=None,
        status="done",
        error_class=None,
    )
    await client.checkpoint_workflow_completion(
        run_id, organization_id, "old", "integrations"
    )
    run = await client.get_workflow_run_by_id(run_id)
    state = dict(run.extra["workflow_completion"], lease_until=time.time() - 1)
    await client.update_workflow_run(run_id, extra={"workflow_completion": state})
    assert run_id in await client.get_overdue_workflow_completions()
    recovered = await client.claim_workflow_completion(run_id, organization_id, "new")
    assert recovered["steps"] == ["integrations"]
    assert recovered["final_extraction"]["status"] == "done"
    await client.checkpoint_workflow_completion(run_id, organization_id, "new", "done")
    assert run_id not in await client.get_overdue_workflow_completions()
    assert (
        await client.claim_workflow_completion(run_id, organization_id, "third") is None
    )
    run = await client.get_workflow_run_by_id(run_id)
    assert run.extra["workflow_completion"]["final_extraction"]["status"] == "done"
    assert "final_extraction" not in run.gathered_context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_step", ["disposition", "integrations", "billing", "pbx"]
)
async def test_recovery_retries_only_failed_steps(
    completion_db, monkeypatch, failed_step
):
    client, _, run_id = completion_db
    await prepare(client, run_id)
    monkeypatch.setattr(workflow_completion, "db_client", client)
    monkeypatch.setattr(workflow_completion, "run_final_extraction", AsyncMock())
    operations = {
        step: AsyncMock() for step in ("disposition", "integrations", "billing", "pbx")
    }
    monkeypatch.setattr(client, "add_call_disposition_code", operations["disposition"])
    for step, function in (
        ("integrations", "run_integrations_post_workflow_run"),
        ("billing", "report_completed_workflow_run_platform_usage"),
        ("pbx", "sync_external_pbx_call_record"),
    ):
        monkeypatch.setattr(workflow_completion, function, operations[step])
    operations[failed_step].side_effect = TimeoutError("Temporarily unavailable")
    with pytest.raises(
        RuntimeError, match=f"Workflow completion failed: {failed_step}"
    ):
        await workflow_completion.process_workflow_completion(None, run_id)
    run = await client.get_workflow_run_by_id(run_id)
    state = run.extra["workflow_completion"]
    assert state["status"] == "running"
    assert set(state["steps"]) == operations.keys() - {failed_step}
    assert all(operation.await_count == 1 for operation in operations.values())

    # A duplicate job must wait for the same lease as the sweeper.
    await workflow_completion.process_workflow_completion(None, run_id)
    assert all(operation.await_count == 1 for operation in operations.values())
    await client.update_workflow_run(
        run_id, extra={"workflow_completion": {**state, "lease_until": time.time() - 1}}
    )
    assert run_id in await client.get_overdue_workflow_completions()
    operations[failed_step].side_effect = None
    await workflow_completion.process_workflow_completion(None, run_id)
    for step, operation in operations.items():
        assert operation.await_count == (2 if step == failed_step else 1)
    run = await client.get_workflow_run_by_id(run_id)
    assert run.extra["workflow_completion"]["status"] == "done"
    assert run_id not in await client.get_overdue_workflow_completions()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["missing_lead_id", "missing_phone", "invalid_mappings"]
)
async def test_terminal_pbx_validation_leaves_recovery(
    completion_db, monkeypatch, failure
):
    client, _, run_id = completion_db
    await prepare(client, run_id)
    identity = {"type": "vicidial", "callerid": "M123", "lead_id": ""}
    disposition = "DNC" if failure == "missing_phone" else "AA"
    mappings = []
    expected_fields = {"status": disposition}
    if failure == "invalid_mappings":
        identity["lead_id"] = "42"
        disposition = "user_hangup"  # No valid native VICIdial status to send.
        mappings = [{"context_path": "lead_name", "destination_field": "bad name)"}]
        expected_fields = {"bad name)": "Ada"}
    await client.update_workflow_run(
        run_id,
        initial_context={"external_pbx_call": identity},
        gathered_context={"mapped_call_disposition": disposition, "lead_name": "Ada"},
    )
    adapter = create_adapter(
        {
            "type": "vicidial",
            "non_agent_api": {
                "url": "https://vici.example.com/non_agent_api.php",
                "username": "test",
                "password": "test",
            },
        }
    )
    update_fields = AsyncMock(wraps=adapter.update_fields)
    monkeypatch.setattr(adapter, "update_fields", update_fields)
    http_session = Mock()
    monkeypatch.setattr(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        http_session,
    )
    monkeypatch.setattr(workflow_completion, "db_client", client)
    monkeypatch.setattr(external_pbx_writeback, "db_client", client)
    monkeypatch.setattr(
        external_pbx_writeback,
        "_adapter_for_organization",
        AsyncMock(return_value=adapter),
    )
    monkeypatch.setattr(
        client,
        "get_workflow_run_configurations",
        AsyncMock(return_value={"external_pbx_field_mappings": mappings}),
    )
    for function in (
        "run_final_extraction",
        "run_integrations_post_workflow_run",
        "report_completed_workflow_run_platform_usage",
    ):
        monkeypatch.setattr(workflow_completion, function, AsyncMock())

    await asyncio.wait_for(
        workflow_completion.process_workflow_completion(None, run_id), timeout=10
    )
    update_fields.assert_awaited_once_with(identity, expected_fields)
    http_session.assert_not_called()
    run = await client.get_workflow_run_by_id(run_id)
    assert run.extra["workflow_completion"]["status"] == "done"
    assert "pbx" in run.extra["workflow_completion"]["steps"]
    future = time.time() + 365 * 24 * 60 * 60
    monkeypatch.setattr(
        "api.db.workflow_completion_client.time", SimpleNamespace(time=lambda: future)
    )
    assert run_id not in await client.get_overdue_workflow_completions()
    await asyncio.wait_for(
        workflow_completion.process_workflow_completion(None, run_id), timeout=10
    )
    update_fields.assert_awaited_once()


@pytest.mark.asyncio
async def test_migration_rebuilds_an_invalid_concurrent_index(test_engine):
    migration = importlib.import_module(
        "api.alembic.versions.d6a83e2b19f0_index_pending_workflow_completions"
    )
    schema = f"completion_index_{uuid4().hex}"
    index = "idx_workflow_runs_pending_completion"

    def upgrade(connection):
        context = MigrationContext.configure(connection)
        with context.begin_transaction(), Operations.context(context):
            migration.upgrade()

    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            await connection.execute(text(f'SET search_path TO "{schema}"'))
            await connection.execute(
                text(
                    "CREATE TABLE workflow_runs (id integer, is_completed boolean, extra jsonb)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO workflow_runs (id, is_completed) VALUES (1, true), (2, true)"
                )
            )
            # A failed concurrent unique build leaves exactly the invalid index
            # state that IF NOT EXISTS would silently retain on retry.
            with pytest.raises(IntegrityError):
                await connection.execute(
                    text(
                        f"CREATE UNIQUE INDEX CONCURRENTLY {index} ON workflow_runs (is_completed)"
                    )
                )
            valid = text(
                f"SELECT indisvalid FROM pg_index WHERE indexrelid = '{index}'::regclass"
            )
            assert await connection.scalar(valid) is False
            await connection.commit()
            await connection.run_sync(upgrade)
            assert await connection.scalar(valid) is True
            definition = await connection.scalar(
                text(f"SELECT pg_get_indexdef('{index}'::regclass)")
            )
            assert "(id)" in definition
            assert "workflow_completion" in definition
        finally:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
