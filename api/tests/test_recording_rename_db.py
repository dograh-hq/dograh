"""Recording renames and workflow references must become visible together."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.models import (
    OrganizationModel,
    UserModel,
    WorkflowDefinitionModel,
    WorkflowModel,
    WorkflowRecordingModel,
)
from api.db.workflow_recording_client import WorkflowRecordingClient
from api.routes import workflow_recording as routes
from api.schemas.workflow_recording import RecordingUpdateRequestSchema


def prompt(alias):
    return {"prompt": f"Play RECORDING_ID: {alias}"}


@pytest.fixture
async def recording_db(test_engine):
    # Independent committed sessions are needed to test concurrent visibility
    # and rollback, which the shared savepoint fixture cannot exercise.
    client = WorkflowRecordingClient.__new__(WorkflowRecordingClient)
    client.async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with client.async_session() as session:
        user = UserModel(provider_id=f"recording-rename-{uuid4()}")
        orgs = [
            OrganizationModel(provider_id=f"recording-rename-{uuid4()}")
            for _ in range(2)
        ]
        session.add_all([user, *orgs])
        await session.flush()
        workflows = [
            WorkflowModel(
                name="Recording rename",
                organization_id=org.id,
                workflow_definition=prompt("welcome"),
            )
            for org in orgs
        ]
        recordings = [
            WorkflowRecordingModel(
                recording_id="welcome",
                organization_id=org.id,
                transcript="Hello",
                storage_key="welcome.wav",
                storage_backend="s3",
                created_by=user.id,
            )
            for org in orgs
        ]
        session.add_all([*workflows, *recordings])
        await session.flush()
        session.add_all(
            [
                WorkflowDefinitionModel(
                    workflow_id=workflow.id,
                    status=status,
                    workflow_json=prompt("welcome"),
                )
                for workflow in workflows
                for status in ("draft", "published", "archived", "legacy")
            ]
        )
        await session.commit()
    org_ids = [org.id for org in orgs]
    workflow_ids = [workflow.id for workflow in workflows]
    try:
        yield SimpleNamespace(
            client=client,
            org_id=org_ids[0],
            other_org_id=org_ids[1],
            org_ids=org_ids,
            workflow_ids=workflow_ids,
            recording_pk=recordings[0].id,
        )
    finally:
        async with client.async_session() as session:
            await session.execute(
                delete(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id.in_(workflow_ids)
                )
            )
            await session.execute(
                delete(WorkflowModel).where(WorkflowModel.id.in_(workflow_ids))
            )
            await session.execute(
                delete(WorkflowRecordingModel).where(
                    WorkflowRecordingModel.organization_id.in_(org_ids)
                )
            )
            await session.execute(
                delete(OrganizationModel).where(OrganizationModel.id.in_(org_ids))
            )
            await session.execute(delete(UserModel).where(UserModel.id == user.id))
            await session.commit()


async def assert_visible_state(db, alias):
    """Read through a separate connection, including the other tenant's rows."""
    expected = {db.org_id: alias, db.other_org_id: "welcome"}
    async with db.client.async_session() as session:
        recordings = await session.execute(
            select(
                WorkflowRecordingModel.organization_id,
                WorkflowRecordingModel.recording_id,
            ).where(WorkflowRecordingModel.organization_id.in_(db.org_ids))
        )
        assert dict(recordings.all()) == expected
        workflows = await session.execute(
            select(
                WorkflowModel.organization_id, WorkflowModel.workflow_definition
            ).where(WorkflowModel.id.in_(db.workflow_ids))
        )
        assert dict(workflows.all()) == {
            org_id: prompt(name) for org_id, name in expected.items()
        }
        definitions = await session.execute(
            select(
                WorkflowModel.organization_id,
                WorkflowDefinitionModel.status,
                WorkflowDefinitionModel.workflow_json,
            )
            .join(
                WorkflowModel,
                WorkflowDefinitionModel.workflow_id == WorkflowModel.id,
            )
            .where(WorkflowModel.id.in_(db.workflow_ids))
        )
        rows = definitions.all()
        assert len(rows) == 8
        for org_id, status, definition in rows:
            assert definition == prompt(
                "welcome" if status == "legacy" else expected[org_id]
            )


@pytest.mark.asyncio
async def test_rename_commits_references_before_invalidating(recording_db, monkeypatch):
    db = recording_db
    entered = [asyncio.Event(), asyncio.Event()]
    resume = [asyncio.Event(), asyncio.Event()]
    original = db.client._replace_recording_id_in_workflows

    async def held_rewrite(*args):
        entered[0].set()
        await asyncio.wait_for(resume[0].wait(), 5)
        result = await original(*args)
        entered[1].set()
        await asyncio.wait_for(resume[1].wait(), 5)
        return result

    async def invalidate(org_id):
        assert org_id == db.org_id
        await assert_visible_state(db, "renamed")

    invalidation = AsyncMock(side_effect=invalidate)
    monkeypatch.setattr(db.client, "_replace_recording_id_in_workflows", held_rewrite)
    monkeypatch.setattr(routes, "db_client", db.client)
    monkeypatch.setattr(routes, "invalidate_recording_cache", invalidation)
    task = asyncio.create_task(
        routes.update_recording(
            db.recording_pk,
            RecordingUpdateRequestSchema(recording_id="renamed"),
            user=SimpleNamespace(selected_organization_id=db.org_id),
        )
    )
    try:
        # Pause both after the recording UPDATE and after the workflow UPDATEs.
        # A concurrent reader must see the original state until the one commit.
        for index in range(2):
            await asyncio.wait_for(entered[index].wait(), 5)
            await assert_visible_state(db, "welcome")
            invalidation.assert_not_awaited()
            resume[index].set()
        response = await asyncio.wait_for(task, 5)
        assert response.recording_id == "renamed"
        invalidation.assert_awaited_once_with(db.org_id)
        await assert_visible_state(db, "renamed")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_failed_reference_update_rolls_back_rename(
    recording_db, monkeypatch, failure
):
    db = recording_db
    original = db.client._replace_recording_id_in_workflows

    async def failed_rewrite(*args):
        await original(*args)
        raise failure("workflow update failed")

    invalidation = AsyncMock()
    monkeypatch.setattr(db.client, "_replace_recording_id_in_workflows", failed_rewrite)
    monkeypatch.setattr(routes, "db_client", db.client)
    monkeypatch.setattr(routes, "invalidate_recording_cache", invalidation)
    expected_error = HTTPException if failure is RuntimeError else failure
    with pytest.raises(expected_error) as error:
        await routes.update_recording(
            db.recording_pk,
            RecordingUpdateRequestSchema(recording_id="renamed"),
            user=SimpleNamespace(selected_organization_id=db.org_id),
        )
    if failure is RuntimeError:
        assert error.value.status_code == 500
    await assert_visible_state(db, "welcome")
    invalidation.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_rejects_other_organizations_recording(recording_db):
    db = recording_db
    assert (
        await db.client.update_recording_id(db.recording_pk, "renamed", db.other_org_id)
        is None
    )
    await assert_visible_state(db, "welcome")


@pytest.mark.asyncio
async def test_concurrent_renames_rewrite_the_latest_alias(
    recording_db, test_engine, monkeypatch
):
    db = recording_db
    entered = asyncio.Event()
    resume = asyncio.Event()
    second_select = asyncio.Event()
    original = db.client._replace_recording_id_in_workflows

    async def held_rewrite(session, old_id, new_id, organization_id):
        if new_id == "first":
            entered.set()
            await asyncio.wait_for(resume.wait(), 5)
        else:
            assert old_id == "first"
        return await original(session, old_id, new_id, organization_id)

    def observe_select(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT"):
            second_select.set()

    monkeypatch.setattr(db.client, "_replace_recording_id_in_workflows", held_rewrite)
    first = asyncio.create_task(
        db.client.update_recording_id(db.recording_pk, "first", db.org_id)
    )
    tasks = [first]
    try:
        await asyncio.wait_for(entered.wait(), 5)
        event.listen(test_engine.sync_engine, "before_cursor_execute", observe_select)
        second = asyncio.create_task(
            db.client.update_recording_id(db.recording_pk, "second", db.org_id)
        )
        tasks.append(second)
        await asyncio.wait_for(second_select.wait(), 5)
        resume.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        await assert_visible_state(db, "second")
    finally:
        if event.contains(
            test_engine.sync_engine, "before_cursor_execute", observe_select
        ):
            event.remove(
                test_engine.sync_engine, "before_cursor_execute", observe_select
            )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
