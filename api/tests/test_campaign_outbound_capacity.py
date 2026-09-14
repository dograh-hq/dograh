"""Dial-boundary admission and ownership under concurrent/cancelled setup."""

import asyncio
import time
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.routes.campaign import (
    CreateCampaignRequest,
    UpdateCampaignRequest,
    _validate_dial_rate,
    _validate_max_concurrency,
    update_campaign,
)
from api.services.call_concurrency import CallConcurrencySlot
from api.services.call_concurrency.rate_limiter import RateLimiter
from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
from api.services.campaign.errors import (
    ConcurrentSlotAcquisitionError,
)

MODULE = "api.services.campaign.campaign_call_dispatcher"


@pytest.fixture
def setup_call():
    dispatcher = CampaignCallDispatcher()
    campaign = SimpleNamespace(
        id=48,
        organization_id=206,
        workflow_id=249,
        created_by=1,
        telephony_configuration_id=55,
        rate_limit_per_second=1,
        state="running",
        orchestrator_metadata={"max_concurrency": 200},
    )
    row = SimpleNamespace(
        id=1, source_uuid="test", context_variables={"phone_number": "+15550000001"}
    )
    run = SimpleNamespace(id=100, logs={})
    slot = CallConcurrencySlot(
        organization_id=206,
        slot_id="slot",
        max_concurrent=200,
        source="campaign:48",
        scope_key="campaign:48",
    )
    provider = SimpleNamespace(
        PROVIDER_NAME="ari",
        WEBHOOK_ENDPOINT="ari",
        from_numbers=["cli-1", "cli-2"],
        initiate_call=AsyncMock(
            return_value=SimpleNamespace(provider_metadata={}, call_id="call")
        ),
    )
    db = MagicMock()
    for name in (
        "update_workflow_run",
        "update_queued_run",
        "mark_campaign_run_dispatched",
        "return_processing_queued_runs_without_workflow",
    ):
        setattr(db, name, AsyncMock())
    db.get_workflow = AsyncMock(return_value=SimpleNamespace(id=249))
    db.get_campaign_by_id = AsyncMock(return_value=campaign)
    db.create_workflow_run = AsyncMock(return_value=run)
    db.claim_queued_runs_for_processing = AsyncMock(return_value=[row])
    concurrency = SimpleNamespace(
        bind_workflow_run=AsyncMock(),
        release_workflow_run_slot=AsyncMock(),
        release_slot=AsyncMock(),
    )
    limiter = SimpleNamespace(
        select_from_number=AsyncMock(return_value="cli-1"),
        acquire_token=AsyncMock(return_value=True),
        get_next_available_slot=AsyncMock(return_value=0.01),
    )
    with (
        patch(f"{MODULE}.db_client", db),
        patch(f"{MODULE}.call_concurrency", concurrency),
        patch(f"{MODULE}.rate_limiter", limiter),
        patch(
            f"{MODULE}.get_backend_endpoints",
            AsyncMock(return_value=("http://test", None)),
        ),
        patch(
            f"{MODULE}.prepare_workflow_run_inputs",
            AsyncMock(return_value=SimpleNamespace(definition_id=1)),
        ),
        patch(
            f"{MODULE}.authorize_workflow_run_start",
            AsyncMock(return_value=SimpleNamespace(has_quota=True)),
        ),
        patch(
            f"{MODULE}.circuit_breaker",
            SimpleNamespace(record_and_evaluate=AsyncMock()),
        ),
        patch.object(
            dispatcher, "get_provider_for_campaign", AsyncMock(return_value=provider)
        ),
        patch.object(
            dispatcher, "acquire_concurrent_slot", AsyncMock(return_value=slot)
        ),
    ):
        yield SimpleNamespace(
            dispatcher=dispatcher,
            campaign=campaign,
            row=row,
            run=run,
            slot=slot,
            provider=provider,
            db=db,
            concurrency=concurrency,
            limiter=limiter,
        )


@pytest.mark.asyncio
async def test_rate_admission_follows_capacity_and_preparation(setup_call):
    s = setup_call
    events = []

    async def acquire(*args, **kwargs):
        events.append("capacity")
        await asyncio.sleep(0.01)
        return s.slot

    async def create(**kwargs):
        events.append("prepare")
        return s.run

    async def token(*args, **kwargs):
        events.append("token")
        assert kwargs["scope_key"] == "campaign:48"
        return True

    async def dial(**kwargs):
        events.append("dial")
        return SimpleNamespace(provider_metadata={})

    s.dispatcher.acquire_concurrent_slot.side_effect = acquire
    s.db.create_workflow_run.side_effect = create
    s.limiter.acquire_token.side_effect = token
    s.provider.initiate_call.side_effect = dial
    assert await s.dispatcher.process_batch(48) == 1
    assert events == ["capacity", "prepare", "token", "dial"]
    s.limiter.select_from_number.assert_awaited_once_with(206, 55, ["cli-1", "cli-2"])
    s.concurrency.release_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_contention_waits_on_same_scope_without_dropping_contact(setup_call):
    s = setup_call
    s.limiter.acquire_token.side_effect = [False, False, True]
    assert await s.dispatcher.process_batch(48) == 1
    for call in s.limiter.get_next_available_slot.await_args_list:
        assert call.kwargs["scope_key"] == "campaign:48"
    s.db.update_queued_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["prepare", "rate", "provider"])
async def test_cancelled_setup_releases_only_when_no_provider_request_started(
    setup_call, where
):
    s = setup_call
    target = {
        "prepare": s.db.get_workflow,
        "rate": s.limiter.acquire_token,
        "provider": s.provider.initiate_call,
    }[where]
    target.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await s.dispatcher.dispatch_call(s.row, s.campaign, s.slot)
    if where == "provider":
        s.concurrency.release_slot.assert_not_awaited()
        assert s.db.update_queued_run.await_args.kwargs["state"] == "failed"
        assert s.db.update_workflow_run.await_args.kwargs["gathered_context"][
            "call_initiation_uncertain"
        ]
        recovery = s.db.update_workflow_run.await_args.kwargs["logs"][
            "campaign_dispatch"
        ]
        assert recovery["outcome"] == "uncertain"
        assert recovery["slot_id"] == s.slot.slot_id
        assert recovery["scope_key"] == s.slot.scope_key
        assert datetime.fromisoformat(recovery["uncertain_at"]).tzinfo is not None
    else:
        s.concurrency.release_slot.assert_awaited_once_with(s.slot)
        s.provider.initiate_call.assert_not_awaited()
        if where == "rate":
            assert s.db.update_queued_run.await_args.kwargs["state"] == "queued"


@pytest.mark.asyncio
async def test_dial_rate_timeout_requeues_and_releases(setup_call):
    s = setup_call
    s.limiter.acquire_token.return_value = False
    s.dispatcher.CAPACITY_WAIT_TIMEOUT = 0
    assert await s.dispatcher.process_batch(48) == 0
    assert s.db.update_queued_run.await_args.kwargs["state"] == "queued"
    s.concurrency.release_slot.assert_awaited_once()
    s.provider.initiate_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_concurrency_is_not_a_failed_contact(setup_call):
    s = setup_call
    s.dispatcher.acquire_concurrent_slot.side_effect = ConcurrentSlotAcquisitionError(
        206, 48, 30
    )
    assert await s.dispatcher.process_batch(48) == 0
    s.db.update_queued_run.assert_not_awaited()
    s.db.return_processing_queued_runs_without_workflow.assert_awaited_once_with([1])


@pytest.mark.asyncio
async def test_batch_deadline_defers_claims_without_failing_campaign(setup_call):
    s = setup_call
    s.dispatcher.BATCH_TIMEOUT = 0.01

    async def capacity(*args, **kwargs):
        await asyncio.Event().wait()

    s.dispatcher.acquire_concurrent_slot.side_effect = capacity
    assert await s.dispatcher.process_batch(48) == 0
    s.db.return_processing_queued_runs_without_workflow.assert_awaited_once_with([1])
    s.db.update_queued_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_dial_does_not_cancel_an_independent_contact(setup_call):
    s = setup_call
    s.db.claim_queued_runs_for_processing.return_value = [
        s.row,
        SimpleNamespace(
            id=2, source_uuid="second", context_variables=s.row.context_variables
        ),
    ]
    s.provider.initiate_call.side_effect = [
        HTTPException(400, "provider rejected call"),
        SimpleNamespace(provider_metadata={}),
    ]
    assert await s.dispatcher.process_batch(48) == 1
    assert s.provider.initiate_call.await_count == 2
    s.concurrency.release_slot.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_error_persistence_still_releases_unstarted_slot(setup_call):
    s = setup_call
    s.limiter.acquire_token.side_effect = RuntimeError("setup failed")
    s.db.update_workflow_run.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await s.dispatcher.dispatch_call(s.row, s.campaign, s.slot)
    s.concurrency.release_slot.assert_awaited_once()
    s.provider.initiate_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_acceptance_persistence_error_keeps_live_capacity(setup_call):
    s = setup_call
    s.db.update_workflow_run.side_effect = RuntimeError(
        "database temporarily unavailable"
    )
    assert await s.dispatcher.process_batch(48) == 1
    s.concurrency.release_slot.assert_not_awaited()
    s.concurrency.release_workflow_run_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_parallelism_is_bounded_and_cleanup_joins_all_siblings(setup_call):
    s = setup_call
    rows = [SimpleNamespace(id=i) for i in range(20)]
    s.db.claim_queued_runs_for_processing.return_value = rows
    s.dispatcher.SETUP_CONCURRENCY = 3
    active = peak = 0
    entered = asyncio.Event()

    async def dial(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if peak == 3:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    async def return_claims(ids):
        assert active == 0
        assert ids == list(range(20))

    s.dispatcher.dispatch_call = AsyncMock(side_effect=dial)
    s.db.return_processing_queued_runs_without_workflow.side_effect = return_claims
    task = asyncio.create_task(s.dispatcher.process_batch(48))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert peak == 3


@pytest.mark.asyncio
async def test_provider_without_caller_ids_can_dial_when_ready(setup_call):
    s = setup_call
    s.provider.from_numbers = []
    s.limiter.select_from_number.return_value = None
    assert await s.dispatcher.process_batch(48) == 1
    assert s.provider.initiate_call.await_args.kwargs["from_number"] is None


def test_request_supports_200_concurrent_and_integer_dial_rate():
    request = CreateCampaignRequest(
        name="test",
        workflow_id=1,
        source_type="csv",
        source_id="x",
        max_concurrency=200,
        rate_limit_per_second=4,
    )
    assert request.max_concurrency == 200
    for rate in (0, -1, 1.5, True):
        with pytest.raises(ValidationError):
            UpdateCampaignRequest(rate_limit_per_second=rate)


@pytest.mark.asyncio
async def test_rate_and_concurrency_enforce_org_limit_only():
    with (
        patch(
            "api.routes.campaign._get_org_concurrent_limit", AsyncMock(return_value=200)
        ),
        patch("api.routes.campaign._get_from_numbers_count", AsyncMock(return_value=2)),
    ):
        await _validate_dial_rate(4, 206)
        assert await _validate_max_concurrency(200, 206, 55)
        with pytest.raises(HTTPException):
            await _validate_dial_rate(201, 206)
        with pytest.raises(HTTPException):
            await _validate_max_concurrency(201, 206, 55)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, expected",
    [
        ({"rate_limit_per_second": 4}, {"rate_limit_per_second": 4}),
        ({"max_concurrency": None}, {"orchestrator_metadata": {}}),
        ({"name": "renamed"}, {"name": "renamed"}),
    ],
)
async def test_update_round_trips_rate_and_preserves_omitted_settings(body, expected):
    campaign = SimpleNamespace(
        id=48,
        state="paused",
        workflow_id=249,
        telephony_configuration_id=55,
        orchestrator_metadata={"max_concurrency": 200},
    )
    db = SimpleNamespace(
        get_campaign=AsyncMock(return_value=campaign),
        update_campaign=AsyncMock(),
        get_workflow_name=AsyncMock(return_value="test"),
    )
    with (
        patch("api.routes.campaign.db_client", db),
        patch("api.routes.campaign._validate_dial_rate", AsyncMock()),
        patch(
            "api.routes.campaign._get_campaign_stats", AsyncMock(return_value=(0, 0))
        ),
        patch(
            "api.routes.campaign._get_telephony_configuration_name",
            AsyncMock(return_value="test"),
        ),
        patch("api.routes.campaign._build_campaign_response", return_value="ok"),
    ):
        await update_campaign(
            48,
            UpdateCampaignRequest(**body),
            SimpleNamespace(selected_organization_id=206),
        )
    db.update_campaign.assert_awaited_once_with(campaign_id=48, **expected)


@pytest.mark.asyncio
async def test_actual_dial_starts_remain_limited_after_a_long_capacity_wait(setup_call):
    s = setup_call
    rl = RateLimiter()
    s.campaign.id = uuid.uuid4().int % 10**9
    s.campaign.organization_id = s.campaign.id
    scope = f"campaign:{s.campaign.id}"
    gate = asyncio.Event()
    waiting = asyncio.Event()
    waiters = 0
    starts = []
    s.db.claim_queued_runs_for_processing.return_value = [
        s.row,
        SimpleNamespace(
            id=2, source_uuid="second", context_variables=s.row.context_variables
        ),
    ]

    async def capacity(*args, **kwargs):
        nonlocal waiters
        waiters += 1
        if waiters == 2:
            waiting.set()
        await gate.wait()
        return s.slot

    async def dial(**kwargs):
        starts.append(time.monotonic())
        return SimpleNamespace(provider_metadata={})

    s.dispatcher.acquire_concurrent_slot.side_effect = capacity
    s.provider.initiate_call.side_effect = dial
    task = None
    try:
        with patch(f"{MODULE}.rate_limiter", rl):
            task = asyncio.create_task(s.dispatcher.process_batch(s.campaign.id))
            await asyncio.wait_for(waiting.wait(), 3)
            # Let an entire rate window pass before both slots become available.
            await asyncio.sleep(1.05)
            gate.set()
            assert await asyncio.wait_for(task, 4) == 2
        assert len(starts) == 2
        assert starts[1] - starts[0] >= 0.95
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        redis = await rl._get_redis()
        await redis.delete(
            f"rate_limit:{scope}", f"caller_id_rotation:{s.campaign.organization_id}:55"
        )
        await rl.close()


@pytest.mark.asyncio
async def test_fast_batch_completion_schedules_next_batch_without_duplicate_jobs():
    from api.services.campaign.campaign_event_protocol import BatchCompletedEvent
    from api.services.campaign.campaign_orchestrator import CampaignOrchestrator

    orchestrator = CampaignOrchestrator(MagicMock())
    campaign = SimpleNamespace(
        id=48, organization_id=206, state="running", orchestrator_metadata={}
    )
    with (
        patch("api.services.campaign.campaign_orchestrator.db_client") as db,
        patch(
            "api.services.campaign.campaign_orchestrator.enqueue_job", AsyncMock()
        ) as enqueue,
        patch("api.services.campaign.campaign_orchestrator.circuit_breaker") as breaker,
    ):
        db.get_campaign_by_id = AsyncMock(return_value=campaign)
        db.has_dispatchable_campaign_runs = AsyncMock(return_value=True)
        db.update_campaign = AsyncMock()
        breaker.is_circuit_open = AsyncMock(return_value=(False, None))
        await orchestrator._schedule_next_batch(48)
        await orchestrator._schedule_next_batch(48)
        assert enqueue.await_count == 1
        await orchestrator._handle_event(
            BatchCompletedEvent(campaign_id=48, processed_count=20)
        )
        assert enqueue.await_count == 2
