from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.utils.enums import EndTaskReason

from api.constants import (
    TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS,
    TEXT_CHAT_INACTIVITY_SWEEP_PAGE_SIZE,
)
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
from api.tasks.function_names import FunctionNames


@pytest.mark.asyncio
async def test_sweep_enqueues_inactive_sessions(monkeypatch):
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
    enqueue_completion = AsyncMock()

    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_inactive_workflow_run_text_sessions",
        get_inactive,
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "_enqueue_inactive_text_chat_completion",
        enqueue_completion,
    )
    await text_chat_inactivity.sweep_inactive_text_chat_sessions({})

    assert get_inactive.await_count == 1
    query = get_inactive.await_args.kwargs
    assert query["limit"] == TEXT_CHAT_INACTIVITY_SWEEP_PAGE_SIZE
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
    assert enqueue_completion.await_count == 2
    first_completion = enqueue_completion.await_args_list[0].kwargs
    assert first_completion["run_id"] == 10
    assert first_completion["expected_revision"] == 2
    assert first_completion["completed_at"] == last_activity_at + timedelta(minutes=30)
    second_completion = enqueue_completion.await_args_list[1].kwargs
    assert second_completion["completed_at"] == last_activity_at + timedelta(minutes=45)


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
    enqueue_completion = AsyncMock()

    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_inactive_workflow_run_text_sessions",
        get_inactive,
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "_enqueue_inactive_text_chat_completion",
        enqueue_completion,
    )

    await text_chat_inactivity.sweep_inactive_text_chat_sessions({})

    enqueue_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_caps_candidate_pages(monkeypatch):
    last_activity_at = datetime.now(UTC) - timedelta(minutes=40)

    def session(run_id: int):
        return SimpleNamespace(
            workflow_run_id=run_id,
            revision=1,
            updated_at=last_activity_at,
            created_at=last_activity_at,
            workflow_run=SimpleNamespace(
                created_at=last_activity_at,
                definition=SimpleNamespace(workflow_configurations={}),
            ),
        )

    get_inactive = AsyncMock(side_effect=[[session(10)], [session(20)], [session(30)]])
    enqueue_completion = AsyncMock()
    monkeypatch.setattr(text_chat_inactivity, "TEXT_CHAT_INACTIVITY_SWEEP_PAGE_SIZE", 1)
    monkeypatch.setattr(text_chat_inactivity, "TEXT_CHAT_INACTIVITY_SWEEP_MAX_PAGES", 2)
    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_inactive_workflow_run_text_sessions",
        get_inactive,
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "_enqueue_inactive_text_chat_completion",
        enqueue_completion,
    )

    await text_chat_inactivity.sweep_inactive_text_chat_sessions({})

    assert get_inactive.await_count == 2
    assert [call.kwargs["after_run_id"] for call in get_inactive.await_args_list] == [
        0,
        10,
    ]
    assert enqueue_completion.await_count == 2


@pytest.mark.asyncio
async def test_sweep_caps_enqueued_completion_jobs(monkeypatch):
    last_activity_at = datetime.now(UTC) - timedelta(minutes=40)
    sessions = [
        SimpleNamespace(
            workflow_run_id=run_id,
            revision=1,
            updated_at=last_activity_at,
            created_at=last_activity_at,
            workflow_run=SimpleNamespace(
                created_at=last_activity_at,
                definition=SimpleNamespace(workflow_configurations={}),
            ),
        )
        for run_id in (10, 20)
    ]
    get_inactive = AsyncMock(return_value=sessions)
    enqueue_completion = AsyncMock()
    monkeypatch.setattr(
        text_chat_inactivity, "TEXT_CHAT_INACTIVITY_SWEEP_ENQUEUE_LIMIT", 1
    )
    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_inactive_workflow_run_text_sessions",
        get_inactive,
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "_enqueue_inactive_text_chat_completion",
        enqueue_completion,
    )

    await text_chat_inactivity.sweep_inactive_text_chat_sessions({})

    get_inactive.assert_awaited_once()
    enqueue_completion.assert_awaited_once()
    assert enqueue_completion.await_args.kwargs["run_id"] == 10


@pytest.mark.asyncio
async def test_completion_job_ignores_session_that_became_active(monkeypatch):
    last_activity_at = datetime.now(UTC) - timedelta(minutes=40)
    text_session = SimpleNamespace(
        workflow_run_id=10,
        revision=2,
        updated_at=last_activity_at,
        created_at=last_activity_at,
        workflow_run=SimpleNamespace(
            mode=WorkflowRunMode.TEXTCHAT.value,
            is_completed=False,
        ),
    )
    complete = AsyncMock(
        side_effect=TextChatSessionRevisionConflictError(
            expected_revision=2,
            actual_revision=3,
        )
    )
    get_organization_id = AsyncMock(return_value=7)
    get_text_session = AsyncMock(return_value=text_session)
    set_run_id = MagicMock()

    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_organization_id_by_workflow_run_id",
        get_organization_id,
    )
    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_workflow_run_text_session",
        get_text_session,
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "complete_text_chat_session",
        complete,
    )
    monkeypatch.setattr(text_chat_inactivity, "set_current_run_id", set_run_id)

    completed_at = last_activity_at + timedelta(minutes=30)
    await text_chat_inactivity.complete_inactive_text_chat_session(
        {},
        run_id=10,
        expected_revision=2,
        completed_at_iso=completed_at.isoformat(),
    )

    set_run_id.assert_called_once_with(10)
    get_organization_id.assert_awaited_once_with(10)
    get_text_session.assert_awaited_once_with(10, organization_id=7)
    completion = complete.await_args.kwargs
    assert completion["run_id"] == 10
    assert completion["text_session"] is text_session
    assert completion["expected_revision"] == 2
    assert completion["completion_reason"] == (
        EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value
    )
    assert completion["completed_at"] == completed_at


@pytest.mark.asyncio
async def test_completion_job_retries_post_run_enqueue_for_completed_session(
    monkeypatch,
):
    last_activity_at = datetime.now(UTC) - timedelta(minutes=40)
    text_session = SimpleNamespace(
        workflow_run_id=10,
        revision=3,
        updated_at=last_activity_at,
        created_at=last_activity_at,
        workflow_run=SimpleNamespace(
            mode=WorkflowRunMode.TEXTCHAT.value,
            is_completed=True,
        ),
    )
    complete = AsyncMock()
    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_organization_id_by_workflow_run_id",
        AsyncMock(return_value=7),
    )
    monkeypatch.setattr(
        text_chat_inactivity.db_client,
        "get_workflow_run_text_session",
        AsyncMock(return_value=text_session),
    )
    monkeypatch.setattr(
        text_chat_inactivity,
        "complete_text_chat_session",
        complete,
    )

    completed_at = last_activity_at + timedelta(minutes=30)
    await text_chat_inactivity.complete_inactive_text_chat_session(
        {},
        run_id=10,
        expected_revision=2,
        completed_at_iso=completed_at.isoformat(),
    )

    complete.assert_awaited_once_with(
        run_id=10,
        text_session=text_session,
        expected_revision=2,
        completion_reason=EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value,
        completed_at=completed_at,
    )


@pytest.mark.asyncio
async def test_enqueue_inactive_completion_uses_revision_scoped_job_id(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr("api.tasks.arq.enqueue_job", enqueue)
    completed_at = datetime.now(UTC)

    await text_chat_inactivity._enqueue_inactive_text_chat_completion(
        run_id=10,
        expected_revision=3,
        completed_at=completed_at,
    )

    enqueue.assert_awaited_once_with(
        FunctionNames.COMPLETE_INACTIVE_TEXT_CHAT_SESSION,
        10,
        3,
        completed_at.isoformat(),
        _job_id="inactive-text-chat-completion-10-3",
    )


def test_pending_assistant_turn_uses_configured_inactivity_timeout():
    last_activity_at = datetime.now(UTC) - timedelta(minutes=2)
    text_session = SimpleNamespace(
        updated_at=last_activity_at,
        created_at=last_activity_at,
        session_data={"status": "pending_assistant_turn"},
        workflow_run=SimpleNamespace(created_at=last_activity_at),
    )

    deadline = text_chat_inactivity._text_chat_inactivity_deadline(
        text_session,
        inactivity_timeout_seconds=60,
    )

    assert deadline == last_activity_at + timedelta(seconds=60)


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
