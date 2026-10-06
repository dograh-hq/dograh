"""Database fencing, concurrency, and tenant checks for run preparation."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.db_client import DBClient
from api.db.models import OrganizationModel, WorkflowModel, WorkflowRunModel


@pytest.fixture
async def preparation_db(test_engine):
    client = DBClient.__new__(DBClient)
    client.async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with client.async_session() as session:
        organization = OrganizationModel(
            provider_id=f"run-model-preparation-test-{uuid4()}"
        )
        session.add(organization)
        await session.flush()
        workflow = WorkflowModel(
            organization_id=organization.id, name="Preparation fixture"
        )
        session.add(workflow)
        await session.flush()
        run = WorkflowRunModel(
            workflow_id=workflow.id,
            name="Preparation fixture",
            mode="twilio",
            initial_context={"existing": "keep", "changed": "old"},
        )
        session.add(run)
        await session.commit()
        organization_id, workflow_id, run_id = organization.id, workflow.id, run.id
    try:
        yield client, organization_id, run_id
    finally:
        async with client.async_session() as session:
            await session.execute(
                delete(WorkflowRunModel).where(WorkflowRunModel.id == run_id)
            )
            await session.execute(
                delete(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            await session.execute(
                delete(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_concurrent_preparation_has_one_owner_and_wrong_owner_cannot_finish(
    preparation_db,
):
    client, organization_id, run_id = preparation_db
    owners = await asyncio.gather(
        *[client.claim_run_model_preparation(run_id, organization_id) for _ in range(4)]
    )
    assert sum(owner is not None for owner in owners) == 1
    owner = next(owner for owner in owners if owner is not None)
    assert not await client.finish_run_model_preparation(
        run_id,
        organization_id,
        "wrong-owner",
        snapshot={"preparation_state": "ready"},
        initial_context_patch={"changed": "wrong"},
    )
    async with client.async_session() as session:
        run = await session.get(WorkflowRunModel, run_id)
        assert run.model_configuration_snapshot["preparation_owner"] == owner
        assert run.initial_context["changed"] == "old"


@pytest.mark.asyncio
async def test_finishing_preparation_atomically_saves_snapshot_and_merges_latest_context(
    preparation_db,
):
    client, organization_id, run_id = preparation_db
    owner = await client.claim_run_model_preparation(run_id, organization_id)
    # Simulate a trusted metadata writer while the network fetch is in flight.
    async with client.async_session() as session:
        await session.execute(
            update(WorkflowRunModel)
            .where(WorkflowRunModel.id == run_id)
            .values(
                initial_context={
                    "existing": "keep",
                    "changed": "old",
                    "trusted_metadata": "arrived",
                }
            )
        )
        await session.commit()
    snapshot = {
        "preparation_state": "ready",
        "services": {"llm": {"model": "pinned"}},
        "pre_call_fetch_outcome": "success",
    }
    assert await client.finish_run_model_preparation(
        run_id,
        organization_id,
        owner,
        snapshot=snapshot,
        initial_context_patch={"changed": "fetched", "new": "value"},
    )
    async with client.async_session() as session:
        run = await session.get(WorkflowRunModel, run_id)
        assert run.model_configuration_snapshot == snapshot
        assert run.initial_context == {
            "existing": "keep",
            "changed": "fetched",
            "new": "value",
            "trusted_metadata": "arrived",
        }
    assert await client.claim_run_model_preparation(run_id, organization_id) is None
    assert not await client.finish_run_model_preparation(
        run_id, organization_id, owner, snapshot={"preparation_state": "failed"}
    )


@pytest.mark.asyncio
async def test_expired_preparation_is_reclaimed_and_old_owner_is_fenced(preparation_db):
    client, organization_id, run_id = preparation_db
    first_owner = await client.claim_run_model_preparation(run_id, organization_id)
    async with client.async_session() as session:
        await session.execute(
            update(WorkflowRunModel)
            .where(WorkflowRunModel.id == run_id)
            .values(
                model_configuration_snapshot={
                    "preparation_state": "preparing",
                    "preparation_owner": first_owner,
                    "preparation_started_at": (
                        datetime.now(UTC) - timedelta(minutes=2)
                    ).isoformat(),
                }
            )
        )
        await session.commit()
    next_owner = await client.claim_run_model_preparation(run_id, organization_id)
    assert next_owner and next_owner != first_owner
    assert not await client.finish_run_model_preparation(
        run_id,
        organization_id,
        first_owner,
        snapshot={"preparation_state": "failed"},
        initial_context_patch={"changed": "stale"},
    )
    assert await client.finish_run_model_preparation(
        run_id,
        organization_id,
        next_owner,
        snapshot={"preparation_state": "ready"},
        initial_context_patch={"changed": "winner"},
    )
    async with client.async_session() as session:
        run = await session.get(WorkflowRunModel, run_id)
        assert run.initial_context["changed"] == "winner"
        assert run.model_configuration_snapshot["preparation_state"] == "ready"


@pytest.mark.asyncio
async def test_preparation_claim_and_finish_are_organization_scoped(preparation_db):
    client, organization_id, run_id = preparation_db
    with pytest.raises(ValueError, match="Workflow run not found"):
        await client.claim_run_model_preparation(run_id, organization_id + 1000000)
    owner = await client.claim_run_model_preparation(run_id, organization_id)
    with pytest.raises(ValueError, match="Workflow run not found"):
        await client.finish_run_model_preparation(
            run_id,
            organization_id + 1000000,
            owner,
            snapshot={"preparation_state": "ready"},
        )
    async with client.async_session() as session:
        snapshot = await session.scalar(
            select(WorkflowRunModel.model_configuration_snapshot).where(
                WorkflowRunModel.id == run_id
            )
        )
        assert snapshot["preparation_state"] == "preparing"
        assert snapshot["preparation_owner"] == owner


@pytest.mark.asyncio
async def test_failed_preparation_is_terminal_and_does_not_refetch(preparation_db):
    client, organization_id, run_id = preparation_db
    owner = await client.claim_run_model_preparation(run_id, organization_id)
    failed = {
        "preparation_state": "failed",
        "error_status": 422,
        "error_message": "Invalid configuration",
    }
    assert await client.finish_run_model_preparation(
        run_id, organization_id, owner, snapshot=failed
    )
    assert (
        await client.claim_run_model_preparation(
            run_id, organization_id, stale_after_seconds=0
        )
        is None
    )
    async with client.async_session() as session:
        assert (
            await session.scalar(
                select(WorkflowRunModel.model_configuration_snapshot).where(
                    WorkflowRunModel.id == run_id
                )
            )
            == failed
        )
