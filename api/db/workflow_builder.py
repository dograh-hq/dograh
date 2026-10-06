"""LangGraph checkpoints through Dograh's SQLAlchemy/asyncpg database layer.

The tables and wire format match langgraph-checkpoint-postgres 3.1.2. Schema
changes belong in Alembic. The async saver uses LangGraph's serializer and base
delta-history implementation, without depending on its Psycopg driver.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_serializable_checkpoint_metadata,
)
from langgraph.checkpoint.serde.types import TASKS, _DeltaSnapshot
from sqlalchemy import delete, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from api.db.base_client import BaseDBClient
from api.db.models import CheckpointBlobModel, CheckpointModel, CheckpointWriteModel

CHECKPOINTS = CheckpointModel.__table__
BLOBS = CheckpointBlobModel.__table__
WRITES = CheckpointWriteModel.__table__


class BuilderBusy(Exception):
    pass


class WorkflowBuilderClient(BaseDBClient):
    @asynccontextmanager
    async def builder_checkpointer(self, thread_id: str):
        """Lease one pooled connection and exclude other writers to this thread.

        A session lock spans the turn, while each saver operation commits its own
        transaction so completed checkpoints survive a later failure/cancellation.
        Never return a connection with a held advisory lock to the pool.
        """
        async with self.engine.connect() as connection:
            try:
                async with connection.begin():
                    acquired = await connection.scalar(
                        text(
                            "SELECT pg_try_advisory_lock(hashtextextended(:thread, 0))"
                        ),
                        {"thread": thread_id},
                    )
            except BaseException:
                # Even cancellation between lock acquisition and commit is safe.
                await connection.invalidate()
                raise
            if not acquired:
                raise BuilderBusy("This conversation is already running")
            try:
                yield SQLAlchemyCheckpointSaver(connection, thread_id)
            finally:
                if not connection.invalidated:
                    try:
                        async with connection.begin():
                            await connection.execute(
                                text(
                                    "SELECT pg_advisory_unlock(hashtextextended(:thread, 0))"
                                ),
                                {"thread": thread_id},
                            )
                    except BaseException:
                        await connection.invalidate()
                        raise


class SQLAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """Async saver bound to one authorized conversation for the duration of a turn."""

    def __init__(self, connection: AsyncConnection, thread_id: str):
        super().__init__()
        self.connection = connection
        self.thread_id = thread_id
        self.lock = asyncio.Lock()

    def _scope(self, config: RunnableConfig) -> dict:
        scope = config["configurable"]
        if scope["thread_id"] != self.thread_id:
            raise ValueError("Checkpointer cannot access another conversation")
        return scope

    def _config(self, namespace: str, checkpoint_id: str) -> RunnableConfig:
        return {
            "configurable": {
                "thread_id": self.thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }

    @asynccontextmanager
    async def _transaction(self):
        # LangGraph may schedule checkpoint and pending-write saves concurrently.
        async with self.lock:
            if self.connection.invalidated:
                raise RuntimeError(
                    "Checkpoint connection lost; resume the conversation"
                )
            async with self.connection.begin():
                yield self.connection

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        scope = self._scope(config)
        query = select(CHECKPOINTS).where(
            CHECKPOINTS.c.thread_id == self.thread_id,
            CHECKPOINTS.c.checkpoint_ns == scope.get("checkpoint_ns", ""),
        )
        if checkpoint_id := get_checkpoint_id(config):
            query = query.where(CHECKPOINTS.c.checkpoint_id == checkpoint_id)
        query = query.order_by(CHECKPOINTS.c.checkpoint_id.desc()).limit(1)
        async with self._transaction() as connection:
            row = (await connection.execute(query)).mappings().first()
            return await self._load(row) if row else None

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        query = select(CHECKPOINTS).where(CHECKPOINTS.c.thread_id == self.thread_id)
        if config is not None:
            scope = self._scope(config)
            if "checkpoint_ns" in scope:
                query = query.where(
                    CHECKPOINTS.c.checkpoint_ns == scope["checkpoint_ns"]
                )
            if checkpoint_id := get_checkpoint_id(config):
                query = query.where(CHECKPOINTS.c.checkpoint_id == checkpoint_id)
        if filter:
            query = query.where(CHECKPOINTS.c.metadata.contains(filter))
        if before is not None:
            self._scope(before)
            query = query.where(CHECKPOINTS.c.checkpoint_id < get_checkpoint_id(before))
        query = query.order_by(CHECKPOINTS.c.checkpoint_id.desc())
        if limit is not None:
            query = query.limit(limit)
        async with self._transaction() as connection:
            rows = (await connection.execute(query)).mappings().all()
            checkpoints = [await self._load(row) for row in rows]
        # Release the connection lock before yielding to callers.
        for checkpoint in checkpoints:
            yield checkpoint

    async def _load(self, row) -> CheckpointTuple:
        namespace = row["checkpoint_ns"]
        checkpoint = row["checkpoint"]
        values = dict(checkpoint.get("channel_values") or {})
        versions = checkpoint["channel_versions"]
        if versions:
            blobs = await self.connection.execute(
                select(BLOBS).where(
                    BLOBS.c.thread_id == self.thread_id,
                    BLOBS.c.checkpoint_ns == namespace,
                    tuple_(BLOBS.c.channel, BLOBS.c.version).in_(
                        [
                            (channel, str(version))
                            for channel, version in versions.items()
                        ]
                    ),
                )
            )
            for blob in blobs.mappings():
                if blob["type"] != "empty":
                    values[blob["channel"]] = self.serde.loads_typed(
                        (blob["type"], blob["blob"])
                    )
        writes = await self.connection.execute(
            select(WRITES)
            .where(
                WRITES.c.thread_id == self.thread_id,
                WRITES.c.checkpoint_ns == namespace,
                WRITES.c.checkpoint_id == row["checkpoint_id"],
            )
            .order_by(WRITES.c.task_id, WRITES.c.idx)
        )
        pending = [
            (
                write["task_id"],
                write["channel"],
                self.serde.loads_typed((write["type"], write["blob"])),
            )
            for write in writes.mappings()
        ]
        # Older checkpoint formats kept pending sends on the parent checkpoint.
        if checkpoint["v"] < 4 and row["parent_checkpoint_id"]:
            sends = await self.connection.execute(
                select(WRITES)
                .where(
                    WRITES.c.thread_id == self.thread_id,
                    WRITES.c.checkpoint_ns == namespace,
                    WRITES.c.checkpoint_id == row["parent_checkpoint_id"],
                    WRITES.c.channel == TASKS,
                )
                .order_by(WRITES.c.task_path, WRITES.c.task_id, WRITES.c.idx)
            )
            pending_sends = [
                self.serde.loads_typed((send["type"], send["blob"]))
                for send in sends.mappings()
            ]
            if pending_sends:
                values[TASKS] = pending_sends
                checkpoint["channel_versions"] = {
                    **versions,
                    TASKS: max(versions.values())
                    if versions
                    else self.get_next_version(None, None),
                }
        return CheckpointTuple(
            self._config(namespace, row["checkpoint_id"]),
            {**checkpoint, "channel_values": values},
            row["metadata"],
            self._config(namespace, row["parent_checkpoint_id"])
            if row["parent_checkpoint_id"]
            else None,
            pending,
        )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        scope = self._scope(config)
        namespace = scope.get("checkpoint_ns", "")
        inline = {}
        blobs = []
        for channel, value in checkpoint["channel_values"].items():
            if value is None or isinstance(value, (str, int, float, bool)):
                inline[channel] = value
                continue
            if isinstance(value, _DeltaSnapshot):
                inline[channel] = True  # Upstream's marker for a serialized snapshot.
            if channel in new_versions:
                kind, blob = self.serde.dumps_typed(value)
                blobs.append(
                    {
                        "thread_id": self.thread_id,
                        "checkpoint_ns": namespace,
                        "channel": channel,
                        "version": str(new_versions[channel]),
                        "type": kind,
                        "blob": blob,
                    }
                )
        statement = insert(CHECKPOINTS).values(
            thread_id=self.thread_id,
            checkpoint_ns=namespace,
            checkpoint_id=checkpoint["id"],
            parent_checkpoint_id=get_checkpoint_id(config),
            checkpoint={**checkpoint, "channel_values": inline},
            metadata=get_serializable_checkpoint_metadata(config, metadata),
        )
        statement = statement.on_conflict_do_update(
            index_elements=list(CHECKPOINTS.primary_key.columns),
            set_={
                "checkpoint": statement.excluded.checkpoint,
                "metadata": statement.excluded.metadata,
            },
        )
        async with self._transaction() as connection:
            if blobs:
                await connection.execute(insert(BLOBS).on_conflict_do_nothing(), blobs)
            await connection.execute(statement)
        return self._config(namespace, checkpoint["id"])

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        scope = self._scope(config)
        rows = []
        for index, (channel, value) in enumerate(writes):
            kind, blob = self.serde.dumps_typed(value)
            rows.append(
                {
                    "thread_id": self.thread_id,
                    "checkpoint_ns": scope.get("checkpoint_ns", ""),
                    "checkpoint_id": scope["checkpoint_id"],
                    "task_id": task_id,
                    "task_path": task_path,
                    "idx": WRITES_IDX_MAP.get(channel, index),
                    "channel": channel,
                    "type": kind,
                    "blob": blob,
                }
            )
        if not rows:
            return
        statement = insert(WRITES)
        if all(channel in WRITES_IDX_MAP for channel, _ in writes):
            statement = statement.on_conflict_do_update(
                index_elements=list(WRITES.primary_key.columns),
                set_={
                    key: statement.excluded[key] for key in ("channel", "type", "blob")
                },
            )
        else:
            statement = statement.on_conflict_do_nothing()
        async with self._transaction() as connection:
            await connection.execute(statement, rows)

    async def adelete_thread(self, thread_id: str) -> None:
        self._scope({"configurable": {"thread_id": thread_id}})
        async with self._transaction() as connection:
            for table in (WRITES, BLOBS, CHECKPOINTS):
                await connection.execute(
                    delete(table).where(table.c.thread_id == thread_id)
                )

    def get_next_version(self, current: str | None, channel: None) -> str:
        version = int(str(current).split(".")[0]) if current is not None else 0
        return f"{version + 1:032}.{uuid4().hex}"
