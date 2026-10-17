import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.telephony import ari_manager
from api.tests.test_run_model_snapshot_db import snapshot_db as snapshot_db_fixture

completion_db = snapshot_db_fixture


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
                "created_at": time.time() - 1200,
                "final_extraction": {"status": "pending"},
            }
        },
    )


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
