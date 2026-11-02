"""Pinning a run's model configuration is first-write-wins and tenant-scoped."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.db_client import DBClient
from api.db.models import OrganizationModel, WorkflowModel, WorkflowRunModel


@pytest.fixture
async def snapshot_db(test_engine):
    client = DBClient.__new__(DBClient)
    client.async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with client.async_session() as session:
        organization = OrganizationModel(provider_id=f"run-model-snapshot-{uuid4()}")
        session.add(organization)
        await session.flush()
        workflow = WorkflowModel(organization_id=organization.id, name="Snapshot")
        session.add(workflow)
        await session.flush()
        run = WorkflowRunModel(
            workflow_id=workflow.id,
            name="Snapshot fixture",
            mode="twilio",
            initial_context={"existing": "keep"},
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
async def test_first_stored_snapshot_wins_for_every_caller(snapshot_db):
    client, organization_id, run_id = snapshot_db
    candidates = [{"version": 3, "pick": index} for index in range(4)]
    stored = await asyncio.gather(
        *[
            client.store_model_configuration_snapshot_if_absent(
                run_id, organization_id, candidate
            )
            for candidate in candidates
        ]
    )
    assert len({result["pick"] for result in stored}) == 1
    async with client.async_session() as session:
        run = await session.get(WorkflowRunModel, run_id)
        assert run.model_configuration_snapshot == stored[0]


@pytest.mark.asyncio
async def test_pre_call_update_replaces_the_pin_wholesale(snapshot_db):
    client, organization_id, run_id = snapshot_db
    await client.store_model_configuration_snapshot_if_absent(
        run_id, organization_id, {"version": 3, "services": {"llm": "pinned"}}
    )
    await client.update_workflow_run(
        run_id,
        model_configuration_snapshot={"version": 3, "services": {"llm": "patched"}},
        initial_context={"fetched": "value"},
    )
    async with client.async_session() as session:
        run = await session.get(WorkflowRunModel, run_id)
        assert run.model_configuration_snapshot == {
            "version": 3,
            "services": {"llm": "patched"},
        }
        assert run.initial_context == {"existing": "keep", "fetched": "value"}


@pytest.mark.asyncio
async def test_storing_a_snapshot_is_organization_scoped(snapshot_db):
    client, organization_id, run_id = snapshot_db
    with pytest.raises(ValueError, match="Workflow run not found"):
        await client.store_model_configuration_snapshot_if_absent(
            run_id, organization_id + 1000000, {"version": 3}
        )
    async with client.async_session() as session:
        run = await session.get(WorkflowRunModel, run_id)
        assert run.model_configuration_snapshot is None
