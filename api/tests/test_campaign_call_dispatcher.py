"""Real DB checks for dispatch claims, retry decisions and campaign completion."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.db import db_client
from api.db.models import (
    CampaignModel,
    OrganizationModel,
    QueuedRunModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.call_concurrency import CallConcurrencySlot
from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
from api.services.campaign.campaign_retry import schedule_campaign_retry


@pytest.fixture(scope="module")
async def sessions(setup_test_database):
    engine = create_async_engine(setup_test_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    old_engine, old_session = db_client.engine, db_client.async_session
    db_client.engine, db_client.async_session = engine, factory
    yield factory
    db_client.engine, db_client.async_session = old_engine, old_session
    await engine.dispose()


@pytest.fixture
async def campaign_data(sessions):
    async with sessions() as session:
        org = OrganizationModel(provider_id=f"capacity-test-{uuid.uuid4().hex}")
        session.add(org)
        await session.flush()
        user = UserModel(
            provider_id=f"capacity-test-{uuid.uuid4().hex}",
            selected_organization_id=org.id,
        )
        session.add(user)
        await session.flush()
        workflow = WorkflowModel(
            name="capacity-test", user_id=user.id, organization_id=org.id
        )
        session.add(workflow)
        await session.flush()
        campaign = CampaignModel(
            name="capacity-test",
            organization_id=org.id,
            workflow_id=workflow.id,
            created_by=user.id,
            source_type="csv",
            source_id="test",
            state="running",
            rate_limit_per_second=4,
        )
        session.add(campaign)
        await session.flush()
        rows = [
            QueuedRunModel(
                campaign_id=campaign.id,
                source_uuid=f"contact-{i}",
                context_variables={"phone_number": f"+15550000{i:03}"},
                state="queued",
            )
            for i in range(10)
        ]
        session.add_all(rows)
        await session.commit()
    yield SimpleNamespace(campaign=campaign, rows=rows, org=org)
    async with sessions() as session:
        await session.execute(
            delete(WorkflowRunModel).where(WorkflowRunModel.campaign_id == campaign.id)
        )
        await session.execute(
            delete(QueuedRunModel).where(QueuedRunModel.campaign_id == campaign.id)
        )
        await session.execute(
            delete(CampaignModel).where(CampaignModel.id == campaign.id)
        )
        await session.execute(
            delete(WorkflowModel).where(WorkflowModel.id == workflow.id)
        )
        await session.execute(delete(UserModel).where(UserModel.id == user.id))
        await session.execute(
            delete(OrganizationModel).where(OrganizationModel.id == org.id)
        )
        await session.commit()


@pytest.fixture
async def dispatcher(sessions, campaign_data):
    instance = CampaignCallDispatcher()
    seen = []

    async def create_run(row, campaign, slot):
        await asyncio.sleep(0.01)
        seen.append(row.id)
        async with sessions() as session:
            run = WorkflowRunModel(
                name="capacity-test",
                workflow_id=campaign.workflow_id,
                mode="ari",
                campaign_id=campaign.id,
                queued_run_id=row.id,
            )
            session.add(run)
            await session.commit()
            return run

    with (
        patch.object(instance, "get_provider_for_campaign", AsyncMock()),
        patch.object(
            instance, "acquire_concurrent_slot", AsyncMock(return_value=object())
        ),
        patch.object(instance, "dispatch_call", AsyncMock(side_effect=create_run)),
    ):
        yield instance, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("sizes", [(5,), (5, 5), (3, 7), (2, 2, 2, 2, 2), (20, 20)])
async def test_parallel_claims_count_every_dispatch_once(
    sizes, dispatcher, campaign_data, sessions
):
    instance, seen = dispatcher
    results = await asyncio.gather(
        *[instance.process_batch(campaign_data.campaign.id, size) for size in sizes]
    )
    expected = min(10, sum(sizes))
    assert sum(results) == len(seen) == len(set(seen)) == expected
    async with sessions() as session:
        campaign = await session.get(CampaignModel, campaign_data.campaign.id)
        rows = list(
            (
                await session.execute(
                    select(QueuedRunModel).where(
                        QueuedRunModel.campaign_id == campaign.id
                    )
                )
            ).scalars()
        )
    assert campaign.processed_rows == expected
    assert sum(row.state == "processed" for row in rows) == expected
    assert all(row.state != "processing" for row in rows)


@pytest.mark.asyncio
async def test_progress_transition_is_idempotent_and_org_scoped(
    dispatcher, campaign_data, sessions
):
    instance, seen = dispatcher
    await instance.process_batch(campaign_data.campaign.id, 1)
    async with sessions() as session:
        row = await session.get(QueuedRunModel, seen[0])
        run_id = (
            await session.execute(
                select(WorkflowRunModel.id).where(
                    WorkflowRunModel.queued_run_id == row.id
                )
            )
        ).scalar_one()
    assert not await db_client.mark_campaign_run_dispatched(
        row.id, run_id, row.campaign_id, campaign_data.org.id + 1000000
    )
    assert not await db_client.mark_campaign_run_dispatched(
        row.id, run_id, row.campaign_id, campaign_data.org.id
    )
    async with sessions() as session:
        campaign = await session.get(CampaignModel, row.campaign_id)
    assert campaign.processed_rows == 1


@pytest.mark.asyncio
async def test_cancelled_batch_returns_real_claims(dispatcher, campaign_data, sessions):
    instance, _ = dispatcher
    started = asyncio.Event()

    async def wait_for_slot(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    instance.acquire_concurrent_slot.side_effect = wait_for_slot
    task = asyncio.create_task(instance.process_batch(campaign_data.campaign.id))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with sessions() as session:
        rows = list(
            (
                await session.execute(
                    select(QueuedRunModel).where(
                        QueuedRunModel.campaign_id == campaign_data.campaign.id
                    )
                )
            ).scalars()
        )
    assert all(row.state == "queued" for row in rows)


@pytest.mark.asyncio
async def test_paused_campaign_never_claims_work(dispatcher, campaign_data):
    instance, seen = dispatcher
    await db_client.update_campaign(campaign_data.campaign.id, state="paused")
    assert await instance.process_batch(campaign_data.campaign.id) == 0
    assert not seen


@pytest.mark.asyncio
async def test_shared_readiness_failure_precedes_claim(dispatcher, campaign_data):
    instance, seen = dispatcher
    instance.get_provider_for_campaign.side_effect = ValueError(
        "incomplete configuration"
    )
    with patch.object(
        db_client, "claim_queued_runs_for_processing", AsyncMock()
    ) as claim:
        with pytest.raises(ValueError):
            await instance.process_batch(campaign_data.campaign.id)
        claim.assert_not_awaited()
    assert not seen


@pytest.mark.asyncio
async def test_campaign_slot_keeps_org_and_campaign_limits():
    campaign = SimpleNamespace(id=48, orchestrator_metadata={"max_concurrency": 200})
    slot = CallConcurrencySlot(
        organization_id=206,
        slot_id="test",
        max_concurrent=250,
        source="campaign:48",
        scope_key="campaign:48",
    )
    with patch(
        "api.services.campaign.campaign_call_dispatcher.call_concurrency"
    ) as concurrency:
        concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        assert (
            await CampaignCallDispatcher().acquire_concurrent_slot(206, campaign)
            == slot
        )
        assert (
            concurrency.acquire_org_slot.await_args.kwargs["scope_max_concurrent"]
            == 200
        )
        assert concurrency.acquire_org_slot.await_args.args == (206,)


@pytest.mark.asyncio
async def test_prior_undialed_workflow_does_not_strand_a_reclaimed_contact(
    campaign_data, sessions
):
    row = campaign_data.rows[0]
    async with sessions() as session:
        session.add(
            WorkflowRunModel(
                name="cancelled setup",
                workflow_id=campaign_data.campaign.workflow_id,
                campaign_id=campaign_data.campaign.id,
                queued_run_id=row.id,
                mode="ari",
                is_completed=True,
                logs={"campaign_dispatch": {"outcome": "not_started"}},
            )
        )
        await session.commit()
    await db_client.update_queued_run(row.id, state="processing")
    assert await db_client.return_processing_queued_runs_without_workflow([row.id]) == 1
    async with sessions() as session:
        requeued = await session.get(QueuedRunModel, row.id)
        assert requeued.state == "queued"


@pytest.fixture
async def finished_campaign(campaign_data, sessions):
    campaign = campaign_data.campaign
    async with sessions() as session:
        await session.execute(
            update(CampaignModel)
            .where(CampaignModel.id == campaign.id)
            .values(
                source_sync_status="completed",
                processed_rows=10,
                total_rows=10,
                last_activity_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(QueuedRunModel)
            .where(QueuedRunModel.campaign_id == campaign.id)
            .values(state="processed")
        )
        runs = [
            WorkflowRunModel(
                name="finished call",
                workflow_id=campaign.workflow_id,
                campaign_id=campaign.id,
                queued_run_id=row.id,
                mode="ari",
                state="completed",
                is_completed=True,
            )
            for row in campaign_data.rows
        ]
        session.add_all(runs)
        await session.commit()
    return SimpleNamespace(**vars(campaign_data), run=runs[0])


@pytest.mark.asyncio
async def test_campaign_completion_is_atomic_and_org_scoped(finished_campaign):
    s = finished_campaign
    assert (
        await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id + 1000000)
        is None
    )
    results = await asyncio.gather(
        *(
            db_client.complete_campaign_if_idle(s.campaign.id, s.org.id)
            for _ in range(3)
        )
    )
    completed = [campaign for campaign in results if campaign is not None]
    assert len(completed) == 1
    assert completed[0].state == "completed"
    assert completed[0].completed_at is not None


@pytest.mark.asyncio
async def test_completion_publishes_and_cleans_up_with_default_sessions(
    finished_campaign,
):
    from api.services.campaign.campaign_orchestrator import CampaignOrchestrator

    campaign = finished_campaign.campaign
    orchestrator = CampaignOrchestrator(AsyncMock())
    publish = AsyncMock()
    orchestrator.publisher.publish_campaign_completed = publish
    orchestrator._processing_locks[campaign.id] = datetime.now(UTC)

    # The application uses the default expire_on_commit=True. Fixture sessions
    # keep attributes loaded, which previously hid the detached-object error.
    with patch.object(db_client, "async_session", async_sessionmaker(db_client.engine)):
        await orchestrator._complete_campaign(campaign)
        await orchestrator._complete_campaign(campaign)

    publish.assert_awaited_once_with(
        campaign_id=campaign.id,
        total_rows=10,
        processed_rows=10,
        failed_rows=0,
        duration_seconds=None,
    )
    assert campaign.id not in orchestrator._processing_locks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,completed",
    [
        ("initialized", False),
        ("running", False),
        ("completed", False),
        ("initialized", True),
        ("running", True),
    ],
)
async def test_campaign_waits_for_all_calls(
    finished_campaign, sessions, state, completed
):
    s = finished_campaign
    async with sessions() as session:
        await session.execute(
            update(WorkflowRunModel)
            .where(WorkflowRunModel.id == s.run.id)
            .values(state=state, is_completed=completed)
        )
        await session.commit()
    assert await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,delay,blocks,dispatchable",
    [
        ("queued", None, True, True),
        ("queued", 120, True, False),
        ("queued", -1, True, True),
        ("processing", None, True, False),
        ("failed", None, False, False),
        ("processed", None, False, False),
    ],
)
async def test_campaign_counts_future_retries_and_processing_rows(
    finished_campaign, sessions, state, delay, blocks, dispatchable
):
    s = finished_campaign
    scheduled = (
        datetime.now(UTC) + timedelta(seconds=delay) if delay is not None else None
    )
    async with sessions() as session:
        await session.execute(
            update(QueuedRunModel)
            .where(QueuedRunModel.id == s.rows[0].id)
            .values(state=state, scheduled_for=scheduled)
        )
        await session.commit()
    assert (
        await db_client.has_dispatchable_campaign_runs(s.campaign.id, s.org.id)
        is dispatchable
    )
    assert not await db_client.has_dispatchable_campaign_runs(
        s.campaign.id, s.org.id + 1000000
    )
    result = await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id)
    assert (result is None) is blocks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,sync_status",
    [
        ("running", "pending"),
        ("running", "in_progress"),
        ("running", "failed"),
        ("syncing", "completed"),
        ("paused", "completed"),
        ("failed", "completed"),
    ],
)
async def test_only_running_fully_synced_campaigns_complete(
    finished_campaign, sessions, state, sync_status
):
    s = finished_campaign
    await db_client.update_campaign(
        s.campaign.id, state=state, source_sync_status=sync_status
    )
    assert await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id) is None


@pytest.mark.asyncio
async def test_empty_synced_campaign_completes(finished_campaign, sessions):
    s = finished_campaign
    async with sessions() as session:
        await session.execute(
            delete(WorkflowRunModel).where(
                WorkflowRunModel.campaign_id == s.campaign.id
            )
        )
        await session.execute(
            delete(QueuedRunModel).where(QueuedRunModel.campaign_id == s.campaign.id)
        )
        await session.commit()
    assert (
        await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id) is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exhausted", [False, True])
async def test_duplicate_retry_decisions_are_atomic_and_block_completion(
    finished_campaign, sessions, exhausted
):
    s = finished_campaign
    await db_client.update_campaign(
        s.campaign.id,
        retry_config={
            "enabled": True,
            "max_retries": 1,
            "retry_delay_seconds": 120,
        },
    )
    await db_client.update_workflow_run(
        s.run.id, state="initialized", is_completed=False
    )
    if exhausted:
        await db_client.update_queued_run(s.rows[0].id, retry_count=1)
    await asyncio.gather(
        *(
            schedule_campaign_retry(s.run, "busy", organization_id=s.org.id)
            for _ in range(3)
        )
    )
    async with sessions() as session:
        retries = list(
            (
                await session.scalars(
                    select(QueuedRunModel).where(
                        QueuedRunModel.parent_queued_run_id == s.rows[0].id
                    )
                )
            ).all()
        )
        run = await session.get(WorkflowRunModel, s.run.id)
        campaign = await session.get(CampaignModel, s.campaign.id)
    assert len(retries) == (0 if exhausted else 1)
    assert campaign.failed_rows == (1 if exhausted else 0)
    assert run.logs["campaign_retry_decision"]["outcome"] == (
        "exhausted" if exhausted else "scheduled"
    )
    assert await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id) is None

    await db_client.update_workflow_run(s.run.id, state="completed", is_completed=True)
    result = await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id)
    assert (result is not None) is exhausted
    if not exhausted:
        assert retries[0].scheduled_for > datetime.now(UTC)
        assert retries[0].context_variables["retry_attempt"] == 1
        assert not await db_client.has_dispatchable_campaign_runs(
            s.campaign.id, s.org.id
        )
        await db_client.update_queued_run(retries[0].id, state="failed")
        assert (
            await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id)
            is not None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config,reason",
    [
        ({"enabled": False}, "busy"),
        ({"retry_on_busy": False}, "busy"),
        ({"retry_on_no_answer": False}, "no_answer"),
        ({"retry_on_voicemail": False}, "voicemail"),
    ],
)
async def test_disabled_retries_do_not_block_completion(
    finished_campaign, config, reason
):
    s = finished_campaign
    await db_client.update_campaign(s.campaign.id, retry_config=config)
    await schedule_campaign_retry(s.run, reason, organization_id=s.org.id)
    assert await db_client.get_queued_runs_count(s.campaign.id, ["queued"]) == 0
    assert (
        await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id) is not None
    )


@pytest.mark.asyncio
async def test_retry_decision_cannot_cross_organizations(finished_campaign):
    s = finished_campaign
    result = await db_client.record_campaign_retry_decision(
        s.run.id, s.campaign.id, s.org.id + 1000000, "busy", datetime.now(UTC)
    )
    assert result is None
    assert await db_client.get_queued_runs_count(s.campaign.id, ["queued"]) == 0
