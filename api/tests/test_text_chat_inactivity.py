from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.utils.enums import EndTaskReason

from api.constants import TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS
from api.db.models import (
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
    WorkflowRunTextSessionModel,
)
from api.enums import WorkflowRunMode
from api.services.workflow.text_chat_session_service import (
    TextChatSessionRevisionConflictError,
)
from api.tasks import text_chat_inactivity


@pytest.mark.asyncio
async def test_sweep_completes_inactive_sessions(monkeypatch):
    last_activity_at = datetime.now(UTC) - timedelta(minutes=50)
    first_session = SimpleNamespace(
        workflow_run_id=10,
        revision=2,
        updated_at=last_activity_at,
        created_at=last_activity_at - timedelta(minutes=5),
        workflow_run=SimpleNamespace(
            created_at=last_activity_at,
            definition=SimpleNamespace(
                workflow_configurations={
                    "text_chat_inactivity_timeout_seconds": 30 * 60
                }
            ),
        ),
    )
    last_session = SimpleNamespace(
        workflow_run_id=20,
        revision=4,
        updated_at=last_activity_at,
        created_at=last_activity_at - timedelta(minutes=5),
        workflow_run=SimpleNamespace(
            created_at=last_activity_at,
            definition=SimpleNamespace(
                workflow_configurations={
                    "text_chat_inactivity_timeout_seconds": 45 * 60
                }
            ),
        ),
    )
    get_inactive = AsyncMock(return_value=[first_session, last_session])
    complete = AsyncMock()
    set_run_id = MagicMock()

    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_inactive_workflow_run_text_sessions",
        get_inactive,
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "complete_text_chat_session",
        complete,
    )
    monkeypatch.setattr(text_chat_inactivity, "set_current_run_id", set_run_id)
    await text_chat_inactivity.sweep_inactive_text_chat_sessions({})

    assert get_inactive.await_count == 1
    query = get_inactive.await_args.kwargs
    assert query["limit"] == 100
    assert query["after_run_id"] == 0
    assert (datetime.now(UTC) - query["active_since"]).total_seconds() == pytest.approx(
        TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS,
        abs=2,
    )
    assert (
        datetime.now(UTC) - query["inactive_before"]
    ).total_seconds() == pytest.approx(
        60,
        abs=2,
    )
    assert complete.await_count == 2
    first_completion = complete.await_args_list[0].kwargs
    assert first_completion["run_id"] == 10
    assert first_completion["expected_revision"] == 2
    assert first_completion["completion_reason"] == (
        EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value
    )
    assert first_completion["completed_at"] == last_activity_at + timedelta(minutes=30)
    second_completion = complete.await_args_list[1].kwargs
    assert second_completion["completed_at"] == last_activity_at + timedelta(minutes=45)
    set_run_id.assert_any_call(10)
    set_run_id.assert_any_call(20)


@pytest.mark.asyncio
async def test_sweep_waits_for_workflow_inactivity_timeout(monkeypatch):
    last_activity_at = datetime.now(UTC) - timedelta(minutes=30)
    text_session = SimpleNamespace(
        workflow_run_id=10,
        revision=2,
        updated_at=last_activity_at,
        created_at=last_activity_at,
        workflow_run=SimpleNamespace(
            created_at=last_activity_at,
            definition=SimpleNamespace(
                workflow_configurations={
                    "text_chat_inactivity_timeout_seconds": 60 * 60
                }
            ),
        ),
    )
    get_inactive = AsyncMock(return_value=[text_session])
    complete = AsyncMock()

    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_inactive_workflow_run_text_sessions",
        get_inactive,
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "complete_text_chat_session",
        complete,
    )

    await text_chat_inactivity.sweep_inactive_text_chat_sessions({})

    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_continues_when_session_becomes_active(monkeypatch):
    last_activity_at = datetime.now(UTC) - timedelta(minutes=40)
    run = SimpleNamespace(
        created_at=last_activity_at,
        definition=SimpleNamespace(workflow_configurations={}),
    )
    conflicted = SimpleNamespace(
        workflow_run_id=10,
        revision=2,
        updated_at=last_activity_at,
        created_at=last_activity_at,
        workflow_run=run,
    )
    completed = SimpleNamespace(
        workflow_run_id=20,
        revision=4,
        updated_at=last_activity_at,
        created_at=last_activity_at,
        workflow_run=run,
    )
    get_inactive = AsyncMock(return_value=[conflicted, completed])
    complete = AsyncMock(
        side_effect=[
            TextChatSessionRevisionConflictError(
                expected_revision=2,
                actual_revision=3,
            ),
            None,
        ]
    )

    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_inactive_workflow_run_text_sessions",
        get_inactive,
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "complete_text_chat_session",
        complete,
    )
    monkeypatch.setattr(
        text_chat_inactivity, "set_current_run_id", lambda _run_id: None
    )

    await text_chat_inactivity.sweep_inactive_text_chat_sessions({})

    assert complete.await_count == 2


@pytest.mark.asyncio
async def test_inactive_session_query_filters_mode_completion_and_cutoff(
    async_session,
    db_session,
):
    now = datetime.now(UTC)
    recent_stale_at = now.replace(microsecond=0) - timedelta(minutes=40)
    old_stale_at = now.replace(microsecond=0) - timedelta(hours=4)
    active_at = now + timedelta(minutes=1)

    organization = OrganizationModel(provider_id="inactive-text-chat-org")
    async_session.add(organization)
    await async_session.flush()
    user = UserModel(
        provider_id="inactive-text-chat-user",
        selected_organization_id=organization.id,
    )
    async_session.add(user)
    await async_session.flush()
    workflow = WorkflowModel(
        name="Inactive text chat workflow",
        user_id=user.id,
        organization_id=organization.id,
        workflow_definition={"nodes": [], "edges": []},
        template_context_variables={},
    )
    async_session.add(workflow)
    await async_session.flush()

    run_specs = [
        ("recent-stale-text", WorkflowRunMode.TEXTCHAT.value, False, recent_stale_at),
        ("old-stale-text", WorkflowRunMode.TEXTCHAT.value, False, old_stale_at),
        ("active-text", WorkflowRunMode.TEXTCHAT.value, False, active_at),
        ("completed-text", WorkflowRunMode.TEXTCHAT.value, True, recent_stale_at),
        ("stale-non-text", "test", False, recent_stale_at),
    ]
    sessions = []
    for name, mode, is_completed, updated_at in run_specs:
        workflow_run = WorkflowRunModel(
            name=name,
            workflow_id=workflow.id,
            mode=mode,
            is_completed=is_completed,
            created_at=updated_at,
        )
        async_session.add(workflow_run)
        await async_session.flush()
        sessions.append(
            WorkflowRunTextSessionModel(
                workflow_run_id=workflow_run.id,
                session_data={},
                checkpoint={},
                created_at=updated_at,
                updated_at=updated_at,
            )
        )
    async_session.add_all(sessions)
    await async_session.flush()

    inactive = await db_session.get_inactive_workflow_run_text_sessions(
        active_since=now
        - timedelta(seconds=TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS),
        inactive_before=now - timedelta(minutes=1),
    )

    assert [session.workflow_run.name for session in inactive] == ["recent-stale-text"]
