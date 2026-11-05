"""Request-independent execution, replay, cancellation and tenant boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from api.schemas.workflow_builder import BuilderSession, BuilderTurnRequest
from api.services.workflow import builder
from api.services.workflow.builder_runtime import execution
from api.tests.support.builder_execution import (
    builder_execution as _builder_execution,  # noqa: F401
)

USER = SimpleNamespace(id=3, provider_id="user-3", selected_organization_id=7)


@pytest.mark.asyncio
async def test_disconnect_does_not_cancel_worker_or_prevent_existing_draft_save(
    builder_execution, monkeypatch
):
    session_id = uuid4()
    release = asyncio.Event()
    finished = BuilderSession(
        id=session_id,
        checkpoint="final",
        workflow_id=41,
        proposal={
            "name": "Agent",
            "code": "source",
            "summary": "Updated",
            "workflow": {},
        },
    )

    async def turn(*args):
        await release.wait()
        yield {"type": "session", "session": finished.model_dump(mode="json")}
        yield {"type": "done"}

    monkeypatch.setattr(builder, "load_builder_workflow", AsyncMock())
    monkeypatch.setattr(builder, "builder_turn", turn)
    save = AsyncMock(return_value=41)
    monkeypatch.setattr(builder, "save_builder_draft", save)
    request = BuilderTurnRequest(
        request_id=uuid4(), message="Change greeting", workflow_id=41
    )
    run = await execution.admit_builder_turn(USER, session_id, request)
    stream = execution.stream_builder_run(USER, session_id, run["id"])
    assert '"type": "run"' in await anext(stream)
    await stream.aclose()
    release.set()
    await asyncio.gather(*builder_execution.tasks)
    assert run["status"] == "completed"
    save.assert_awaited_once()
    events = builder_execution.events[run["id"]]
    assert [e["event"]["type"] for e in events][-2:] == ["session", "done"]
    saved = next(e for e in events if e["event"]["type"] == "saved")
    replay = [
        event
        async for event in execution.stream_builder_run(
            USER, session_id, run["id"], after=saved["seq"] - 1
        )
    ]
    assert '"type": "saved"' in replay[1]
    await execution.admit_builder_turn(USER, session_id, request)
    assert len(builder_execution.tasks) == 1


@pytest.mark.asyncio
async def test_restarted_worker_continues_checkpoint_without_repeating_message(
    builder_execution, monkeypatch
):
    session_id, request_id = uuid4(), uuid4()
    request = BuilderTurnRequest(
        request_id=request_id, message="Edit greeting", checkpoint="old"
    )
    # Persist admission but simulate the first worker exiting after checkpointing.
    run = await execution.db_client.admit_builder_run(
        **execution._scope(USER, session_id), request=request.model_dump(mode="json")
    )
    run.update(attempts=1, status="running")
    current = BuilderSession(
        id=session_id,
        checkpoint="advanced",
        can_continue=True,
        pending=[
            {
                "id": "preview",
                "kind": "mcp",
                "tool_name": "preview_workflow",
                "arguments": {"code": "source"},
            }
        ],
    )
    monkeypatch.setattr(builder, "load_session", AsyncMock(return_value=current))
    seen = []

    async def turn(actor, id, resumed):
        seen.append(resumed)
        yield {
            "type": "session",
            "session": BuilderSession(id=id, checkpoint="done").model_dump(mode="json"),
        }
        yield {"type": "done"}

    monkeypatch.setattr(builder, "builder_turn", turn)
    await execution.execute_builder_run(str(request_id))
    assert seen[0].message is None
    assert seen[0].checkpoint == "advanced"
    assert run["status"] == "completed"


@pytest.mark.asyncio
async def test_stop_cancels_worker_and_returns_preserved_session(
    builder_execution, monkeypatch
):
    session_id = uuid4()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def turn(*args):
        started.set()
        try:
            await asyncio.Event().wait()
            yield {}
        finally:
            cancelled.set()

    monkeypatch.setattr(builder, "builder_turn", turn)
    monkeypatch.setattr(
        builder,
        "load_session",
        AsyncMock(
            return_value=BuilderSession(
                id=session_id, checkpoint="working", can_continue=True
            )
        ),
    )
    run = await execution.admit_builder_turn(
        USER, session_id, BuilderTurnRequest(request_id=uuid4(), message="Edit")
    )
    await started.wait()
    assert await execution.cancel_run(USER, session_id, UUID(run["id"])) == {
        "cancel_requested": True
    }
    await asyncio.wait_for(asyncio.gather(*builder_execution.tasks), timeout=3)
    assert cancelled.is_set()
    assert run["status"] == "cancelled"
    events = builder_execution.events[run["id"]]
    assert events[-2]["event"]["session"]["checkpoint"] == "working"
    assert events[-1]["event"]["type"] == "cancelled"


@pytest.mark.asyncio
async def test_run_events_and_stop_are_scoped_to_org_user_and_session(
    builder_execution, monkeypatch
):
    monkeypatch.setattr(execution, "enqueue_builder_run", AsyncMock())
    id = uuid4()
    run = await execution.admit_builder_turn(
        USER, id, BuilderTurnRequest(request_id=uuid4(), message="Build")
    )
    for actor, session in [
        (SimpleNamespace(id=4, selected_organization_id=7), id),
        (SimpleNamespace(id=3, selected_organization_id=8), id),
        (USER, uuid4()),
    ]:
        with pytest.raises(HTTPException) as error:
            await execution.cancel_run(actor, session, UUID(run["id"]))
        assert error.value.status_code == 404
    assert not run["cancel_requested"]


@pytest.mark.asyncio
async def test_revoked_membership_prevents_background_mutation(
    builder_execution, monkeypatch
):
    monkeypatch.setattr(
        execution.db_client,
        "is_user_member_of_organization",
        AsyncMock(return_value=False),
    )
    turn = AsyncMock()
    monkeypatch.setattr(builder, "builder_turn", turn)
    run = await execution.admit_builder_turn(
        USER, uuid4(), BuilderTurnRequest(request_id=uuid4(), message="Build")
    )
    await asyncio.gather(*builder_execution.tasks)
    assert run["status"] == "failed"
    turn.assert_not_called()


@pytest.mark.asyncio
async def test_queued_run_reports_waiting_without_a_worker(
    builder_execution, monkeypatch
):
    import json

    monkeypatch.setattr(execution, "enqueue_builder_run", AsyncMock())
    monkeypatch.setattr(execution.asyncio, "sleep", AsyncMock())
    session_id = uuid4()
    run = await execution.admit_builder_turn(
        USER, session_id, BuilderTurnRequest(request_id=uuid4(), message="Build")
    )
    stream = execution.stream_builder_run(USER, session_id, run["id"])
    try:
        assert json.loads((await anext(stream))[6:])["type"] == "run"
        queued = json.loads((await anext(stream))[6:])
        assert queued["type"] == "status"
        assert "queued" in queued["message"]
        waiting = json.loads((await anext(stream))[6:])
        assert "still waiting" in waiting["message"]
        assert "Thinking" not in waiting["message"]
        assert run["attempts"] == 0
        run["status"] = "running"
        await execution.db_client.append_builder_event(
            run["id"], {"type": "status", "message": "Thinking about your agent…"}
        )
        assert "Thinking about your agent" in await anext(stream)
    finally:
        await stream.aclose()
