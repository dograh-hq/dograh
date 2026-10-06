"""ARI hangups finalize unconnected runs while preserving connected outcomes."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import TelephonyCallStatus, WorkflowRunState
from api.services.campaign import campaign_retry
from api.services.telephony import ari_manager, status_processor
from api.tasks.function_names import FunctionNames

ORG_ID = 206
CONFIG_ID = 55
RUN_ID = 2485


def _ari_connection():
    from api.services.telephony.ari_manager import ARIConnection

    return ARIConnection(
        organization_id=ORG_ID,
        telephony_configuration_id=CONFIG_ID,
        ari_endpoint="http://asterisk:8088",
        app_name="dograh",
        app_password="secret",
    )


@pytest.fixture
def terminal_call(monkeypatch, no_disposition_mapping):
    connection = _ari_connection()
    channel_id = "1789335496.125"
    mappings = {channel_id: str(RUN_ID)}
    run = SimpleNamespace(
        id=RUN_ID,
        workflow=SimpleNamespace(organization_id=ORG_ID),
        mode="ari",
        call_type="outbound",
        initial_context={"telephony_configuration_id": CONFIG_ID},
        gathered_context={"call_id": channel_id, "call_tags": ["existing"]},
        logs={},
        campaign_id=49,
        queued_run_id=205,
        state=WorkflowRunState.INITIALIZED.value,
        is_completed=False,
        usage_info={},
    )

    async def get_mapping(channel):
        await asyncio.sleep(0)
        return mappings.get(channel)

    async def delete_mapping(*channels):
        for channel in channels:
            mappings.pop(channel, None)

    async def get_run(run_id, *, organization_id):
        await asyncio.sleep(0)
        return (
            run
            if (run_id, organization_id) == (run.id, run.workflow.organization_id)
            else None
        )

    async def update_run(run_id, **updates):
        assert run_id == run.id
        for key, value in updates.items():
            if isinstance(value, dict):
                getattr(run, key).update(value)
            else:
                setattr(run, key, value)

    db = SimpleNamespace(
        get_workflow_run=AsyncMock(side_effect=get_run),
        get_workflow_run_by_id=AsyncMock(return_value=run),
        update_workflow_run=AsyncMock(side_effect=update_run),
    )
    release = AsyncMock(return_value=True)
    retry = AsyncMock()
    notify_completed = AsyncMock()
    breaker = SimpleNamespace(record_and_evaluate=AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr(
        connection, "_get_channel_run", AsyncMock(side_effect=get_mapping)
    )
    monkeypatch.setattr(
        connection, "_delete_channel_run", AsyncMock(side_effect=delete_mapping)
    )
    monkeypatch.setattr(
        connection, "_get_transfer_id_for_channel", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(ari_manager, "db_client", db)
    monkeypatch.setattr(status_processor, "db_client", db)
    monkeypatch.setattr(
        ari_manager,
        "call_concurrency",
        SimpleNamespace(release_workflow_run_slot=release),
    )
    monkeypatch.setattr(
        status_processor,
        "campaign_call_dispatcher",
        SimpleNamespace(release_call_slot=release),
    )
    monkeypatch.setattr(status_processor, "circuit_breaker", breaker)
    monkeypatch.setattr(
        status_processor,
        "schedule_campaign_retry",
        retry,
    )
    monkeypatch.setattr(status_processor, "enqueue_job", enqueue)
    monkeypatch.setattr(
        status_processor, "notify_campaign_call_completed", notify_completed
    )
    return SimpleNamespace(
        connection=connection,
        channel_id=channel_id,
        run=run,
        db=db,
        mappings=mappings,
        release=release,
        retry=retry,
        notify_completed=notify_completed,
        breaker=breaker,
        enqueue=enqueue,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cause", "expected_status", "retry_reason"),
    [
        (17, TelephonyCallStatus.BUSY, "busy"),
        (18, TelephonyCallStatus.NO_ANSWER, "no_answer"),
        (19, TelephonyCallStatus.NO_ANSWER, "no_answer"),
        (16, TelephonyCallStatus.CANCELED, None),
        (1, TelephonyCallStatus.FAILED, None),
        (21, TelephonyCallStatus.FAILED, None),
    ],
)
async def test_channel_destroyed_before_stasis_returns_its_reservation(
    terminal_call, cause, expected_status, retry_reason
):
    call = terminal_call
    await call.connection._release_destroyed_channel(
        call.channel_id, cause, "PBX hangup"
    )

    assert call.run.state == WorkflowRunState.COMPLETED.value
    assert call.run.is_completed is True
    assert call.run.gathered_context["call_status"] == expected_status.value
    assert call.run.gathered_context["call_disposition"] == expected_status.value
    assert call.run.gathered_context["mapped_call_disposition"] == expected_status.value
    assert call.run.gathered_context["call_tags"] == [
        "existing",
        "not_connected",
        f"telephony_{expected_status.value}",
    ]
    assert call.run.usage_info == {"call_duration_seconds": 0}
    assert call.run.logs["telephony_status_callbacks"][0]["ari_cause"] == cause
    call.release.assert_awaited_once_with(RUN_ID)
    assert call.mappings == {}
    call.notify_completed.assert_awaited_once_with(49, RUN_ID)
    # Unconnected calls trigger integrations without platform call billing.
    call.enqueue.assert_awaited_once_with(
        FunctionNames.RUN_INTEGRATIONS_POST_WORKFLOW_RUN, RUN_ID
    )
    if retry_reason:
        call.retry.assert_awaited_once_with(
            call.run,
            retry_reason,
            organization_id=ORG_ID,
        )
    else:
        call.retry.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cause", "expected_status"),
    [(17, TelephonyCallStatus.BUSY), (19, TelephonyCallStatus.NO_ANSWER)],
)
async def test_campaign_without_queue_link_still_completes(
    terminal_call, monkeypatch, cause, expected_status
):
    call = terminal_call
    call.run.queued_run_id = None
    call.db.get_campaign = AsyncMock(
        return_value=SimpleNamespace(id=49, state="running", retry_config={})
    )
    call.db.get_queued_run_by_id = AsyncMock(return_value=None)
    call.db.record_campaign_retry_decision = AsyncMock()
    monkeypatch.setattr(campaign_retry, "db_client", call.db)
    monkeypatch.setattr(
        status_processor,
        "schedule_campaign_retry",
        campaign_retry.schedule_campaign_retry,
    )

    await call.connection._release_destroyed_channel(
        call.channel_id, cause, "PBX hangup"
    )

    assert call.run.state == WorkflowRunState.COMPLETED.value
    assert call.run.is_completed is True
    assert call.run.gathered_context["call_status"] == expected_status.value
    assert call.run.gathered_context["call_disposition"] == expected_status.value
    call.db.get_queued_run_by_id.assert_not_awaited()
    call.db.record_campaign_retry_decision.assert_not_awaited()
    call.release.assert_awaited_once_with(RUN_ID)
    assert call.mappings == {}
    call.notify_completed.assert_awaited_once_with(49, RUN_ID)
    call.enqueue.assert_awaited_once_with(
        FunctionNames.RUN_INTEGRATIONS_POST_WORKFLOW_RUN, RUN_ID
    )


@pytest.mark.asyncio
async def test_retry_decision_finishes_before_run_becomes_terminal(terminal_call):
    call = terminal_call
    started, proceed = asyncio.Event(), asyncio.Event()

    async def wait_for_retry(*args, **kwargs):
        started.set()
        await proceed.wait()

    call.retry.side_effect = wait_for_retry
    task = asyncio.create_task(
        call.connection._release_destroyed_channel(call.channel_id, 17, "User busy")
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert call.run.state == WorkflowRunState.INITIALIZED.value
        assert not call.run.is_completed
        call.notify_completed.assert_not_awaited()
    finally:
        proceed.set()
        await task

    assert call.run.is_completed
    call.notify_completed.assert_awaited_once_with(49, RUN_ID)


@pytest.mark.asyncio
async def test_failed_retry_write_does_not_make_run_terminal(terminal_call):
    call = terminal_call
    call.retry.side_effect = RuntimeError("retry insert failed")
    await call.connection._release_destroyed_channel(call.channel_id, 17, "User busy")
    assert not call.run.is_completed
    assert call.channel_id in call.mappings
    call.notify_completed.assert_not_awaited()
    call.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_destroyed_events_do_not_duplicate_terminal_side_effects(
    terminal_call,
):
    call = terminal_call
    await asyncio.gather(
        *(
            call.connection._release_destroyed_channel(call.channel_id, 17, "User busy")
            for _ in range(3)
        )
    )

    assert call.run.is_completed
    assert len(call.run.logs["telephony_status_callbacks"]) == 1
    call.release.assert_awaited_once_with(RUN_ID)
    call.retry.assert_awaited_once()
    call.breaker.record_and_evaluate.assert_awaited_once()
    call.enqueue.assert_awaited_once()
    assert call.mappings == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", [WorkflowRunState.RUNNING.value, WorkflowRunState.COMPLETED.value]
)
async def test_destroyed_channel_keeps_pipeline_outcome(terminal_call, state):
    call = terminal_call
    call.run.state = state
    call.run.gathered_context["call_disposition"] = "voicemail_detected"

    await call.connection._release_destroyed_channel(
        call.channel_id, 16, "Normal Clearing"
    )

    call.db.update_workflow_run.assert_not_awaited()
    assert call.run.gathered_context["call_disposition"] == "voicemail_detected"
    call.enqueue.assert_not_awaited()
    call.retry.assert_not_awaited()
    call.release.assert_awaited_once_with(RUN_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guard", ["media_setup", "inbound", "completed_flag", "other_channel"]
)
async def test_destroyed_channel_keeps_existing_call_outcome(terminal_call, guard):
    call = terminal_call
    if guard == "media_setup":
        call.run.gathered_context["ext_channel_id"] = "ext-123"
    elif guard == "inbound":
        call.run.call_type = "inbound"
    elif guard == "completed_flag":
        call.run.is_completed = True
    else:
        call.run.gathered_context["call_id"] = "another-channel"

    await call.connection._release_destroyed_channel(
        call.channel_id, 16, "Normal Clearing"
    )

    call.db.update_workflow_run.assert_not_awaited()
    call.enqueue.assert_not_awaited()
    call.release.assert_awaited_once_with(RUN_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("entered_stasis", [False, True])
async def test_destroyed_event_respects_stasis_before_setup_persists(
    terminal_call, monkeypatch, entered_stasis
):
    call = terminal_call
    tasks = []
    create_task = asyncio.create_task

    def track_task(coroutine):
        task = create_task(coroutine)
        tasks.append(task)
        return task

    monkeypatch.setattr(ari_manager.asyncio, "create_task", track_task)
    monkeypatch.setattr(
        call.connection, "_is_ext_channel", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(call.connection, "_handle_stasis_start", AsyncMock())
    if entered_stasis:
        await call.connection._handle_event(
            json.dumps(
                {
                    "type": "StasisStart",
                    "channel": {"id": call.channel_id, "state": "Up"},
                    "args": [f"workflow_run_id={RUN_ID}", "workflow_id=249"],
                }
            )
        )
    await call.connection._handle_event(
        json.dumps(
            {
                "type": "ChannelDestroyed",
                "channel": {"id": call.channel_id},
                "cause": 19,
                "cause_txt": "User alerting, no answer",
            }
        )
    )
    await asyncio.gather(*tasks)

    assert call.run.is_completed is (not entered_stasis)
    if entered_stasis:
        call.db.update_workflow_run.assert_not_awaited()
        call.enqueue.assert_not_awaited()
    else:
        assert call.run.gathered_context["call_status"] == "no-answer"
        call.enqueue.assert_awaited_once()
    call.release.assert_awaited_once_with(RUN_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_scope", ["organization", "configuration", "provider"])
async def test_destroyed_event_does_not_touch_another_scope(
    terminal_call, foreign_scope
):
    call = terminal_call
    if foreign_scope == "organization":
        call.run.workflow.organization_id += 1
    elif foreign_scope == "configuration":
        call.run.initial_context["telephony_configuration_id"] += 1
    else:
        call.run.mode = "twilio"

    await call.connection._release_destroyed_channel(call.channel_id, 17, "User busy")

    call.db.update_workflow_run.assert_not_awaited()
    call.release.assert_not_awaited()
    call.enqueue.assert_not_awaited()
    assert call.channel_id in call.mappings


@pytest.mark.asyncio
async def test_terminal_write_failure_keeps_mapping_for_repeated_event(terminal_call):
    call = terminal_call
    update_run = call.db.update_workflow_run.side_effect
    call.db.update_workflow_run.side_effect = RuntimeError("database unavailable")

    await call.connection._release_destroyed_channel(call.channel_id, 19, "No answer")
    assert call.channel_id in call.mappings
    assert not call.run.is_completed

    call.db.update_workflow_run.side_effect = update_run
    await call.connection._release_destroyed_channel(call.channel_id, 19, "No answer")
    assert call.run.is_completed
    assert call.mappings == {}
    call.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_channel_we_never_originated_is_left_alone():
    # Inbound legs and anything else Asterisk destroys have no mapping, and
    # inventing a release for them would free capacity a live call is using.
    connection = _ari_connection()

    with (
        patch.object(connection, "_get_channel_run", AsyncMock(return_value=None)),
        patch(
            "api.services.telephony.ari_manager.call_concurrency"
        ) as mock_concurrency,
    ):
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        await connection._release_destroyed_channel("other.1", 16, "Normal Clearing")

    mock_concurrency.release_workflow_run_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_completed_call_releases_only_once():
    # StasisEnd already released and deleted the mapping, so the ChannelDestroyed
    # that follows finds nothing. Idempotence comes from the mapping, not from a
    # flag that could drift.
    connection = _ari_connection()

    with (
        patch.object(connection, "_get_channel_run", AsyncMock(return_value=None)),
        patch(
            "api.services.telephony.ari_manager.call_concurrency"
        ) as mock_concurrency,
    ):
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        await connection._release_destroyed_channel("done.1", 16, "Normal Clearing")

    mock_concurrency.release_workflow_run_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_redis_failure_does_not_escape_the_event_loop():
    # This runs as a fire-and-forget task off the event stream; an exception
    # here would be an unretrieved task exception, not a handled error.
    connection = _ari_connection()

    with patch.object(
        connection,
        "_get_channel_run",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        await connection._release_destroyed_channel("x.1", 1, "Unallocated")
