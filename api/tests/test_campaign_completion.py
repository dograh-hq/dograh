"""Event-driven completion, retry ordering, and periodic recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.campaign import campaign_event_publisher, campaign_orchestrator
from api.services.campaign.campaign_event_protocol import (
    BatchCompletedEvent,
    CallCompletedEvent,
    SyncCompletedEvent,
    parse_campaign_event,
)


@pytest.fixture
def completion(monkeypatch):
    campaign = SimpleNamespace(
        id=48,
        organization_id=206,
        state="running",
        source_sync_status="completed",
        orchestrator_metadata={},
        started_at=datetime.now(UTC) - timedelta(seconds=60),
        processed_rows=20,
        total_rows=20,
        failed_rows=0,
    )
    db = SimpleNamespace(
        get_campaign_by_id=AsyncMock(return_value=campaign),
        get_campaigns_by_status=AsyncMock(return_value=[campaign]),
        complete_campaign_if_idle=AsyncMock(return_value=campaign),
        has_dispatchable_campaign_runs=AsyncMock(return_value=False),
        recover_stale_campaign_claims=AsyncMock(return_value=0),
        update_campaign=AsyncMock(),
    )
    enqueue = AsyncMock()
    orchestrator = campaign_orchestrator.CampaignOrchestrator(AsyncMock())
    orchestrator.publisher = SimpleNamespace(publish_campaign_completed=AsyncMock())
    monkeypatch.setattr(campaign_orchestrator, "db_client", db)
    monkeypatch.setattr(campaign_orchestrator, "enqueue_job", enqueue)
    monkeypatch.setattr(
        campaign_orchestrator,
        "campaign_call_dispatcher",
        SimpleNamespace(recover_stale_dispatches=AsyncMock()),
    )
    monkeypatch.setattr(
        campaign_orchestrator,
        "circuit_breaker",
        SimpleNamespace(is_circuit_open=AsyncMock(return_value=(False, None))),
    )
    return SimpleNamespace(
        campaign=campaign, db=db, enqueue=enqueue, orchestrator=orchestrator
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_class", [CallCompletedEvent, BatchCompletedEvent, SyncCompletedEvent]
)
async def test_completion_event_checks_database_without_inactivity_delay(
    completion, event_class
):
    s = completion
    if event_class is BatchCompletedEvent:
        s.orchestrator._batch_in_progress[s.campaign.id] = datetime.now(UTC)

    await s.orchestrator._handle_event(event_class(campaign_id=s.campaign.id))

    s.db.complete_campaign_if_idle.assert_awaited_once_with(48, 206)
    s.orchestrator.publisher.publish_campaign_completed.assert_awaited_once()
    s.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_completion_does_not_clear_a_running_batch(completion):
    s = completion
    s.orchestrator._batch_in_progress[48] = datetime.now(UTC)
    await s.orchestrator._handle_event(CallCompletedEvent(campaign_id=48))
    s.db.complete_campaign_if_idle.assert_not_awaited()
    s.enqueue.assert_not_awaited()
    assert 48 in s.orchestrator._batch_in_progress


@pytest.mark.asyncio
async def test_future_retry_waits_without_empty_batch_loop(completion):
    s = completion
    s.db.complete_campaign_if_idle.return_value = None
    await s.orchestrator._handle_event(BatchCompletedEvent(campaign_id=48))
    await s.orchestrator._check_stale_campaigns()
    s.enqueue.assert_not_awaited()
    s.orchestrator.publisher.publish_campaign_completed.assert_not_awaited()

    # When the retry becomes due, the next recovery scan dispatches it.
    s.db.has_dispatchable_campaign_runs.return_value = True
    await s.orchestrator._check_stale_campaigns()
    s.enqueue.assert_awaited_once()
    assert 48 in s.orchestrator._batch_in_progress


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_batch", [False, True])
async def test_periodic_scan_recovers_missed_completion_event(completion, stale_batch):
    s = completion
    if stale_batch:
        s.orchestrator._batch_in_progress[48] = datetime.now(UTC) - timedelta(
            seconds=301
        )
    await s.orchestrator._check_stale_campaigns()
    s.db.complete_campaign_if_idle.assert_awaited_once_with(48, 206)
    s.orchestrator.publisher.publish_campaign_completed.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_event_only_announces_successful_completion_transition(
    completion,
):
    s = completion
    s.db.complete_campaign_if_idle.side_effect = [s.campaign, None]
    await s.orchestrator._handle_event(CallCompletedEvent(campaign_id=48))
    await s.orchestrator._handle_event(CallCompletedEvent(campaign_id=48))
    s.orchestrator.publisher.publish_campaign_completed.assert_awaited_once()


@pytest.mark.asyncio
async def test_campaign_can_complete_outside_dialing_window(completion, monkeypatch):
    s = completion
    monkeypatch.setattr(s.orchestrator, "_is_within_schedule", lambda campaign: False)
    await s.orchestrator._handle_event(CallCompletedEvent(campaign_id=48))
    s.enqueue.assert_not_awaited()
    s.orchestrator.publisher.publish_campaign_completed.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_completed_notification_round_trips(monkeypatch):
    redis = AsyncMock()
    publisher = campaign_event_publisher.CampaignEventPublisher(redis)
    monkeypatch.setattr(
        campaign_event_publisher,
        "get_campaign_event_publisher",
        AsyncMock(return_value=publisher),
    )
    await campaign_event_publisher.notify_campaign_call_completed(48, 123)
    event = parse_campaign_event(redis.publish.await_args.args[1])
    assert isinstance(event, CallCompletedEvent)
    assert (event.campaign_id, event.workflow_run_id) == (48, 123)


@pytest.mark.asyncio
async def test_notification_failure_does_not_fail_call_finalization(monkeypatch):
    publisher = SimpleNamespace(
        publish_call_completed=AsyncMock(side_effect=ConnectionError)
    )
    monkeypatch.setattr(
        campaign_event_publisher,
        "get_campaign_event_publisher",
        AsyncMock(return_value=publisher),
    )
    await campaign_event_publisher.notify_campaign_call_completed(48, 123)
    publisher.publish_call_completed.assert_awaited_once_with(48, 123)


@pytest.mark.asyncio
async def test_stalled_notification_does_not_hold_up_call_cleanup(monkeypatch):
    async def stalled_publish(*args):
        await asyncio.Event().wait()

    publisher = SimpleNamespace(
        publish_call_completed=AsyncMock(side_effect=stalled_publish)
    )
    monkeypatch.setattr(
        campaign_event_publisher, "CALL_COMPLETION_EVENT_TIMEOUT_SECONDS", 0.01
    )
    monkeypatch.setattr(
        campaign_event_publisher,
        "get_campaign_event_publisher",
        AsyncMock(return_value=publisher),
    )
    await asyncio.wait_for(
        campaign_event_publisher.notify_campaign_call_completed(48, 123), 1
    )
    publisher.publish_call_completed.assert_awaited_once_with(48, 123)
