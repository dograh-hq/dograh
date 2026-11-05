"""Durable worker orchestration with an in-memory repository for HTTP tests.

Database locking, admission and receipts are covered separately against PostgreSQL.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.db.workflow_builder import BuilderBusy
from api.services.workflow.builder_runtime import execution


@pytest.fixture(name="builder_execution")
async def builder_execution(monkeypatch):
    rows, events, tasks, locked = {}, {}, [], set()

    def matches(row, scope):
        return all(row[key] == value for key, value in scope.items())

    async def admit(*, request, **scope):
        key = request["request_id"]
        if key in rows:
            if not matches(rows[key], scope):
                raise ValueError("Request ID belongs to another conversation")
            return rows[key]
        if any(
            matches(row, scope) and row["status"] in execution.ACTIVE
            for row in rows.values()
        ):
            raise BuilderBusy("Already running")
        rows[key] = dict(
            id=key,
            request=request,
            status="queued",
            attempts=0,
            cancel_requested=False,
            **scope,
        )
        events[key] = []
        return rows[key]

    async def get(run_id, **scope):
        row = rows.get(run_id)
        return row if row and matches(row, scope) else None

    async def latest(**scope):
        return next(
            (row for row in reversed(list(rows.values())) if matches(row, scope)), None
        )

    @asynccontextmanager
    async def claim(run_id):
        row = rows.get(run_id)
        if run_id in locked or not row or row["status"] not in execution.ACTIVE:
            yield None
            return
        locked.add(run_id)
        row["attempts"] += 1
        row["status"] = "running"
        try:
            yield dict(row)
        finally:
            locked.remove(run_id)

    async def append(run_id, event, *, status=None):
        events[run_id].append({"seq": len(events[run_id]) + 1, "event": event})
        if status:
            rows[run_id]["status"] = status
        return len(events[run_id])

    async def read(run_id, *, after=0, **scope):
        return (
            [row for row in events.get(run_id, []) if row["seq"] > after][:50]
            if await get(run_id, **scope)
            else []
        )

    async def cancel(run_id, **scope):
        row = await get(run_id, **scope)
        if row and row["status"] in execution.ACTIVE:
            row["cancel_requested"] = True
            return True
        return False

    async def heartbeat(run_id):
        return rows[run_id]["cancel_requested"]

    async def enqueue(run_id):
        tasks.append(asyncio.create_task(execution.execute_builder_run(run_id)))

    for name, method in {
        "admit_builder_run": admit,
        "get_builder_run": get,
        "latest_builder_run": latest,
        "claim_builder_run": claim,
        "append_builder_event": append,
        "builder_run_events": read,
        "cancel_builder_run": cancel,
        "heartbeat_builder_run": heartbeat,
    }.items():
        monkeypatch.setattr(execution.db_client, name, method)
    monkeypatch.setattr(
        execution.db_client,
        "get_user_by_id",
        AsyncMock(
            side_effect=lambda id: SimpleNamespace(id=id, provider_id=f"user-{id}")
        ),
    )
    monkeypatch.setattr(
        execution.db_client,
        "is_user_member_of_organization",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(execution, "enqueue_builder_run", enqueue)
    yield SimpleNamespace(rows=rows, events=events, tasks=tasks, enqueue=enqueue)
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
