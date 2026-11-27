"""Durable admission, worker locks and atomic tool receipts on test PostgreSQL."""

import asyncio
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.db.builder_run_client import BuilderRunClient
from api.db.models import Base, ToolModel, UserModel
from api.db.tool_client import ToolClient
from api.db.workflow_builder import BuilderBusy

MIGRATION = import_module("api.alembic.versions.93d7a618f2c4_durable_builder_runs")


class HarnessClient(BuilderRunClient, ToolClient):
    pass


@pytest.fixture
async def run_client(test_engine):
    schema = f"builder_run_test_{uuid4().hex}"
    async with test_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        test_engine.url,
        pool_size=2,
        max_overflow=0,
        connect_args={"server_settings": {"search_path": schema}},
    )
    client = HarnessClient.__new__(HarnessClient)
    client.engine = engine
    client.async_session = async_sessionmaker(bind=engine)

    def migrate(connection):
        with Operations.context(MigrationContext.configure(connection)):
            MIGRATION.upgrade()
        ToolModel.__table__.create(connection)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE organizations (id INTEGER PRIMARY KEY)")
            )
            await conn.run_sync(UserModel.__table__.create)
            await conn.execute(text("INSERT INTO organizations VALUES (1), (2)"))
            await conn.execute(
                text(
                    "INSERT INTO users (id, provider_id) VALUES (3, 'test-three'), (4, 'test-four')"
                )
            )
            await conn.run_sync(migrate)
        yield client
    finally:
        await engine.dispose()
        async with test_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def scope():
    return {"organization_id": 1, "user_id": 3, "session_id": str(uuid4())}


@pytest.mark.asyncio
async def test_admission_is_atomic_first_request_wins_and_scope_is_enforced(run_client):
    owner = scope()
    request = {"request_id": str(uuid4()), "message": "Original"}
    rows = await asyncio.gather(
        *(run_client.admit_builder_run(**owner, request=request) for _ in range(3))
    )
    assert len({row["id"] for row in rows}) == 1
    replay = await run_client.admit_builder_run(
        **owner, request={**request, "message": "Changed"}
    )
    assert replay["request"]["message"] == "Original"
    with pytest.raises(BuilderBusy):
        await run_client.admit_builder_run(
            **owner, request={"request_id": str(uuid4())}
        )
    with pytest.raises(ValueError):
        await run_client.admit_builder_run(
            **{**owner, "organization_id": 2}, request=request
        )
    assert (
        await run_client.get_builder_run(rows[0]["id"], **{**owner, "user_id": 4})
        is None
    )
    assert await run_client.latest_builder_run(**owner) == replay


@pytest.mark.asyncio
async def test_worker_lock_events_cancellation_and_recovery(run_client):
    owner = scope()
    id = str(uuid4())
    await run_client.admit_builder_run(**owner, request={"request_id": id})
    async with run_client.claim_builder_run(id) as first:
        assert first["attempts"] == 1
        async with run_client.claim_builder_run(id) as duplicate:
            assert duplicate is None
        await run_client.append_builder_event(id, {"type": "status"})
        assert await run_client.cancel_builder_run(id, **owner)
        assert await run_client.heartbeat_builder_run(id)
    async with run_client.claim_builder_run(id) as recovered:
        assert recovered["attempts"] == 2
        assert recovered["cancel_requested"]
        await run_client.append_builder_event(
            id, {"type": "cancelled"}, status="cancelled"
        )
    assert await run_client.builder_run_events(id, **owner, after=1) == [
        {"seq": 2, "event": {"type": "cancelled"}}
    ]
    assert (
        await run_client.builder_run_events(id, **{**owner, "organization_id": 2}) == []
    )
    async with run_client.claim_builder_run(id) as completed:
        assert completed is None


@pytest.mark.asyncio
async def test_cancelled_worker_releases_connection_lock(run_client):
    owner = scope()
    id = str(uuid4())
    await run_client.admit_builder_run(**owner, request={"request_id": id})
    started = asyncio.Event()

    async def work():
        async with run_client.claim_builder_run(id):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with run_client.claim_builder_run(id) as recovered:
        assert recovered["attempts"] == 2


@pytest.mark.asyncio
async def test_atomic_receipts_prevent_duplicate_create_and_replayed_update(run_client):
    args = {
        "organization_id": 1,
        "user_id": 3,
        "name": "Calculator",
        "category": "calculator",
        "definition": {"type": "calculator"},
    }
    id = str(uuid4())
    first, replay = await asyncio.gather(
        *(run_client.create_tool(**args, operation_id=id) for _ in range(2))
    )
    assert first.tool_uuid == replay.tool_uuid
    assert len(await run_client.get_tools_for_organization(1)) == 1
    edit = str(uuid4())
    await run_client.update_tool(
        first.tool_uuid, 1, name="First edit", operation_id=edit
    )
    await run_client.update_tool(first.tool_uuid, 1, name="Later edit")
    replay = await run_client.update_tool(
        first.tool_uuid, 1, name="First edit", operation_id=edit
    )
    assert replay.name == "First edit"
    # Read directly without eager-loading the intentionally minimal users fixture.
    async with run_client.async_session() as session:
        current = await session.get(ToolModel, first.id)
        assert current.name == "Later edit"
    with pytest.raises(ValueError):
        await run_client.get_tool_mutation_receipt(id, 2)


@pytest.mark.asyncio
async def test_migration_matches_models_and_downgrades_cleanly(run_client):
    tables = {
        "workflow_builder_runs",
        "workflow_builder_run_events",
        "tool_mutation_receipts",
    }

    def check(connection):
        def included(obj, name, *args):
            return (
                name in tables
                if obj.__class__.__name__ == "Table"
                else getattr(getattr(obj, "table", None), "name", None) in tables
            )

        context = MigrationContext.configure(
            connection, opts={"include_object": included}
        )
        metadata = MetaData()
        for name in ("organizations", "users"):
            Table(name, metadata, Column("id", Integer, primary_key=True))
        ToolModel.__table__.to_metadata(metadata)
        for name in tables:
            Base.metadata.tables[name].to_metadata(metadata)
        assert compare_metadata(context, metadata) == []
        with Operations.context(context):
            MIGRATION.downgrade()
            MIGRATION.upgrade()

    async with run_client.engine.begin() as connection:
        await connection.run_sync(check)


@pytest.mark.asyncio
async def test_stop_queued_run_finishes_without_worker_and_cannot_be_claimed(
    run_client,
):
    owner = scope()
    run_id = str(uuid4())
    await run_client.admit_builder_run(**owner, request={"request_id": run_id})
    assert not await run_client.cancel_builder_run(
        run_id, **{**owner, "organization_id": 2}
    )
    assert await run_client.cancel_builder_run(run_id, **owner)
    run = await run_client.get_builder_run(run_id, **owner)
    assert run["status"] == "cancelled"
    assert run["attempts"] == 0
    async with run_client.claim_builder_run(run_id) as claimed:
        assert claimed is None
    events = await run_client.builder_run_events(run_id, **owner)
    assert events == [
        {
            "seq": 1,
            "event": {
                "type": "cancelled",
                "message": "Stopped before the builder started.",
            },
        }
    ]
    assert not await run_client.cancel_builder_run(run_id, **owner)
    # The conversation immediately accepts another request.
    next_run = await run_client.admit_builder_run(
        **owner, request={"request_id": str(uuid4())}
    )
    assert next_run["status"] == "queued"
