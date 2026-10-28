"""Marking a run running is initialized-only and tenant-scoped.

The status callback reads 'initialized' as "the pipeline never started" when it
decides whether an unanswered call still needs its post-call integrations, so
the transition must not fire early or reopen a run a callback already ended.
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.db_client import DBClient
from api.db.models import OrganizationModel, WorkflowModel, WorkflowRunModel
from api.enums import WorkflowRunState


@pytest.fixture
async def run_db(test_engine):
    client = DBClient.__new__(DBClient)
    client.async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with client.async_session() as session:
        organization = OrganizationModel(provider_id=f"run-state-running-{uuid4()}")
        session.add(organization)
        await session.flush()
        workflow = WorkflowModel(organization_id=organization.id, name="Run state")
        session.add(workflow)
        await session.flush()
        run = WorkflowRunModel(
            workflow_id=workflow.id, name="Run state fixture", mode="telnyx"
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


async def _state(client, run_id) -> str:
    async with client.async_session() as session:
        return (await session.get(WorkflowRunModel, run_id)).state


@pytest.mark.asyncio
async def test_initialized_run_moves_to_running(run_db):
    client, organization_id, run_id = run_db

    assert await client.mark_workflow_run_running(run_id, organization_id) is True
    assert await _state(client, run_id) == WorkflowRunState.RUNNING.value

    # Already running: nothing to move.
    assert await client.mark_workflow_run_running(run_id, organization_id) is False


@pytest.mark.asyncio
async def test_completed_run_is_not_reopened(run_db):
    client, organization_id, run_id = run_db
    await client.update_workflow_run(
        run_id, is_completed=True, state=WorkflowRunState.COMPLETED.value
    )

    assert await client.mark_workflow_run_running(run_id, organization_id) is False
    assert await _state(client, run_id) == WorkflowRunState.COMPLETED.value


@pytest.mark.asyncio
async def test_marking_running_is_organization_scoped(run_db):
    client, organization_id, run_id = run_db

    assert (
        await client.mark_workflow_run_running(run_id, organization_id + 1000000)
        is False
    )
    assert await _state(client, run_id) == WorkflowRunState.INITIALIZED.value
