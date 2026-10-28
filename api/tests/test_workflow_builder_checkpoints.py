"""Persistence contract and migration checks against the dedicated test database."""

import asyncio
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.types import ERROR, _DeltaSnapshot
from sqlalchemy import MetaData, func, inspect, select, text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import create_async_engine

from api.db.models import Base
from api.db.workflow_builder import BLOBS, CHECKPOINTS, WRITES, WorkflowBuilderClient
from api.services.workflow.builder_runtime.agent import build_agent
from api.services.workflow.builder_runtime.schemas import BuilderStep
from api.services.workflow.builder_runtime.service import run_step
from api.tests.support.workflow_authoring import BRIEF, PLAN
from api.tests.test_workflow_builder_runtime import ScriptedModel, tool_call

MIGRATION = import_module(
    "api.alembic.versions.8b24d9a76c10_add_workflow_builder_checkpoints"
)


def migrate(connection, direction):
    with Operations.context(MigrationContext.configure(connection)):
        getattr(MIGRATION, direction)()


@pytest.fixture
async def checkpoint_client(test_engine):
    schema = f"builder_test_{uuid4().hex}"
    async with test_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    # A small real pool catches transaction leaks and session-lock reuse bugs.
    engine = create_async_engine(
        test_engine.url,
        pool_size=2,
        max_overflow=0,
        connect_args={"server_settings": {"search_path": schema}},
    )
    client = WorkflowBuilderClient.__new__(WorkflowBuilderClient)
    client.engine = engine
    try:
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "upgrade")
        yield client
    finally:
        await engine.dispose()
        async with test_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def config(thread="workflow-builder:1:1:session", namespace=""):
    return {"configurable": {"thread_id": thread, "checkpoint_ns": namespace}}


def checkpoint(values, versions=None):
    result = empty_checkpoint()
    result["channel_values"] = values
    result["channel_versions"] = versions or dict.fromkeys(values, "00000001.1")
    return result


@pytest.mark.asyncio
async def test_roundtrip_history_pending_writes_and_namespace_isolation(
    checkpoint_client,
):
    thread = config()["configurable"]["thread_id"]
    message = AIMessage(
        content="Hello", additional_kwargs={"reasoning_content": "reasoning"}
    )
    values = {
        "messages": [message],
        "binary": b"\x00\xff",
        "active": True,
        "count": 1,
        "optional": None,
        "delta": _DeltaSnapshot(["seed"]),
    }
    first = checkpoint(values)
    async with checkpoint_client.builder_checkpointer(thread) as saver:
        saved = await saver.aput(
            config(), first, {"source": "input", "step": 0}, first["channel_versions"]
        )
        await saver.aput_writes(saved, [("delta", ["next"])], "task", "path")
        # Normal writes are immutable on retry; error slots can be replaced.
        await saver.aput_writes(saved, [("delta", ["duplicate"])], "task", "path")
        await saver.aput_writes(saved, [(ERROR, "first failure")], "task")
        await saver.aput_writes(saved, [(ERROR, "retry failure")], "task")
        second = checkpoint(
            {**values, "count": 2},
            {**first["channel_versions"], "count": "00000002.1"},
        )
        latest = await saver.aput(
            saved, second, {"source": "loop", "step": 1}, {"count": "00000002.1"}
        )
        # Identical checkpoint IDs and versions in another namespace must not collide.
        nested = {
            **first,
            "channel_values": {"messages": [AIMessage(content="Nested")]},
        }
        nested_saved = await saver.aput(
            config(namespace="nested"),
            nested,
            {"source": "input", "step": 0},
            first["channel_versions"],
        )
        await saver.aput_writes(nested_saved, [("delta", ["nested write"])], "task")

    async with checkpoint_client.builder_checkpointer(thread) as saver:
        restored = await saver.aget_tuple(saved)
        assert restored.checkpoint == first
        assert restored.pending_writes == [
            ("task", ERROR, "retry failure"),
            ("task", "delta", ["next"]),
        ]
        newest = await saver.aget_tuple(config())
        assert newest.config == latest
        assert newest.parent_config == saved
        assert newest.checkpoint == second
        assert (await saver.aget_tuple(nested_saved)).checkpoint["channel_values"][
            "messages"
        ][0].content == "Nested"
        delta = await saver.aget_delta_channel_history(
            config=latest, channels=["delta"]
        )
        assert delta["delta"] == {
            "seed": _DeltaSnapshot(["seed"]),
            "writes": [("task", "delta", ["next"])],
        }
        assert [item.config async for item in saver.alist(config(), limit=1)] == [
            latest
        ]
        assert [item.config async for item in saver.alist(config(), before=latest)] == [
            saved
        ]
        assert [
            item.config
            async for item in saver.alist(config(), filter={"source": "input"})
        ] == [saved]
        assert [item async for item in saver.alist(config(), limit=0)] == []
        assert len([item async for item in saver.alist(None)]) == 3
        version = saver.get_next_version(None, None)
        assert saver.get_next_version(version, None) > version


@pytest.mark.asyncio
async def test_parallel_read_tasks_survive_database_checkpoint_reload(
    checkpoint_client,
):
    session_id = uuid4()
    thread = f"workflow-builder:12:3:{session_id}"
    graph_config = config(thread)
    reads = [
        tool_call("get_voice_prompting_guide", {"topic": topic}, topic).tool_calls[0]
        for topic in ("common_guidelines", "turn_taking")
    ]
    model = ScriptedModel(
        organization_id=12,
        user_id="3",
        replies=[
            tool_call("submit_brief", {"brief": BRIEF}, "brief"),
            AIMessage(content="", tool_calls=reads),
            tool_call("submit_plan", {"plan": PLAN}, "plan"),
            tool_call(
                "propose_workflow",
                {"candidate": {"code": "source", "summary": "A receptionist"}},
                "candidate",
            ),
        ],
    )
    specs = [
        {
            "name": "get_voice_prompting_guide",
            "inputSchema": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
            },
        }
    ]

    async def step(request=None):
        async with checkpoint_client.builder_checkpointer(thread) as saver:
            return await run_step(
                build_agent(model, saver, specs), graph_config, session_id, request
            )

    approval = await step(
        BuilderStep(request_id=uuid4(), message="Build a receptionist")
    )
    pending = approval["pending"][0]
    current = await step(
        BuilderStep(
            request_id=uuid4(),
            checkpoint=approval["checkpoint"],
            resume={pending["id"]: {"decision": "approve", "brief_revision": 1}},
        )
    )
    assert len(current["pending"]) == 2
    assert await step() == current
    result = await step(
        BuilderStep(
            request_id=uuid4(),
            checkpoint=current["checkpoint"],
            resume={
                item["id"]: item["arguments"]["topic"] for item in current["pending"]
            },
        )
    )
    assert result["pending"][0]["tool_name"] == "preview_workflow"
    async with checkpoint_client.builder_checkpointer(thread) as saver:
        state = await build_agent(model, saver, specs).aget_state(graph_config)
    assert {
        m.tool_call_id: m.content
        for m in state.values["plan_conversation_messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id in {c["id"] for c in reads}
    } == {c["id"]: c["args"]["topic"] for c in reads}
    assert len(model.seen) == 4


@pytest.mark.asyncio
async def test_failed_checkpoint_save_rolls_back_its_blobs(checkpoint_client):
    thread = config()["configurable"]["thread_id"]
    state = checkpoint({"messages": ["must roll back"]})
    async with checkpoint_client.builder_checkpointer(thread) as saver:
        with pytest.raises(StatementError):
            await saver.aput(
                config(), state, {"unserializable": object()}, state["channel_versions"]
            )
        assert await saver.aget_tuple(config()) is None
    async with checkpoint_client.engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(BLOBS)) == 0


@pytest.mark.asyncio
async def test_scope_and_thread_deletion(checkpoint_client):
    first_thread = config()["configurable"]["thread_id"]
    other_config = config(thread="workflow-builder:2:1:session")
    other_thread = other_config["configurable"]["thread_id"]
    state = checkpoint({"value": ["keep"]})
    async with checkpoint_client.builder_checkpointer(other_thread) as saver:
        await saver.aput(other_config, state, {}, state["channel_versions"])
    async with checkpoint_client.builder_checkpointer(first_thread) as saver:
        assert await saver.aget_tuple(config()) is None
        for namespace in ("", "nested"):
            saved = await saver.aput(
                config(namespace=namespace), state, {}, state["channel_versions"]
            )
            await saver.aput_writes(saved, [("value", "write")], "task")
        with pytest.raises(ValueError, match="another conversation"):
            await saver.aget_tuple(other_config)
        with pytest.raises(ValueError, match="another conversation"):
            await saver.aput(other_config, state, {}, state["channel_versions"])
        with pytest.raises(ValueError, match="another conversation"):
            await saver.aput_writes(other_config, [("value", "write")], "task")
        with pytest.raises(ValueError, match="another conversation"):
            await anext(saver.alist(other_config))
        with pytest.raises(ValueError, match="another conversation"):
            await saver.adelete_thread(other_thread)
        await saver.adelete_thread(first_thread)
    async with checkpoint_client.engine.connect() as connection:
        for table in (CHECKPOINTS, BLOBS, WRITES):
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.thread_id == first_thread)
                )
                == 0
            )
    async with checkpoint_client.builder_checkpointer(other_thread) as saver:
        assert (await saver.aget_tuple(other_config)).checkpoint == state


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_completed_checkpoints_survive_failure_and_locks_return_to_pool(
    checkpoint_client, cancel
):
    thread = config()["configurable"]["thread_id"]
    state = checkpoint({"messages": ["durable"]})
    ready = asyncio.Event()

    async def turn():
        async with checkpoint_client.builder_checkpointer(thread) as saver:
            await saver.aput(config(), state, {}, state["channel_versions"])
            ready.set()
            if cancel:
                await asyncio.Event().wait()
            raise RuntimeError("model failed")

    task = asyncio.create_task(turn())
    await asyncio.wait_for(ready.wait(), timeout=5)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await task
    # Hold both physical connections so acquisition cannot hide a leaked lock
    # by reusing the same PostgreSQL session (session locks are reentrant).
    async with (
        checkpoint_client.engine.connect() as first,
        checkpoint_client.engine.connect() as second,
    ):
        for connection in (first, second):
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
                    )
                )
                == 0
            )
    async with checkpoint_client.builder_checkpointer(thread) as saver:
        assert (await saver.aget_tuple(config())).checkpoint == state


@pytest.mark.asyncio
async def test_invalidated_connection_cannot_reconnect_without_conversation_lock(
    checkpoint_client,
):
    thread = config()["configurable"]["thread_id"]
    async with checkpoint_client.builder_checkpointer(thread) as saver:
        await saver.connection.invalidate()
        with pytest.raises(RuntimeError, match="connection lost"):
            await saver.aget_tuple(config())
    async with checkpoint_client.builder_checkpointer(thread) as saver:
        assert await saver.aget_tuple(config()) is None


@pytest.mark.asyncio
async def test_migration_adopts_existing_data_matches_models_and_downgrades(
    checkpoint_client,
):
    thread = config()["configurable"]["thread_id"]
    state = checkpoint({"messages": ["preserve"]})
    async with checkpoint_client.builder_checkpointer(thread) as saver:
        saved = await saver.aput(config(), state, {}, state["channel_versions"])
        await saver.aput_writes(saved, [("messages", ["pending"])], "task")
    async with checkpoint_client.engine.begin() as connection:
        # Reapplying the adoption logic must leave all checkpoint data intact.
        await connection.run_sync(migrate, "upgrade")

        def schema_diff(sync_connection):
            metadata = MetaData()
            for name in (
                "checkpoints",
                "checkpoint_blobs",
                "checkpoint_writes",
                "checkpoint_migrations",
            ):
                Base.metadata.tables[name].to_metadata(metadata)
            return compare_metadata(
                MigrationContext.configure(
                    sync_connection, opts={"compare_server_default": True}
                ),
                metadata,
            )

        assert await connection.run_sync(schema_diff) == []
    async with checkpoint_client.builder_checkpointer(thread) as saver:
        restored = await saver.aget_tuple(config())
        assert restored.checkpoint == state
        assert restored.pending_writes == [("task", "messages", ["pending"])]
    async with checkpoint_client.engine.begin() as connection:
        await connection.run_sync(migrate, "downgrade")
        assert (
            await connection.run_sync(lambda conn: inspect(conn).get_table_names())
            == []
        )
        await connection.run_sync(migrate, "upgrade")
        assert set(
            await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        ) == {
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
            "checkpoint_migrations",
        }
