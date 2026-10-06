"""Durable recovery of uncertain calls that never receive a provider callback."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import update

from api.db import db_client
from api.db.models import QueuedRunModel, WorkflowRunModel
from api.services.call_concurrency.rate_limiter import FLEET_CONCURRENT_KEY, RateLimiter
from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
from api.services.campaign.campaign_orchestrator import CampaignOrchestrator
from api.tests.test_campaign_call_dispatcher import (
    campaign_data as campaign_data,
)
from api.tests.test_campaign_call_dispatcher import (
    finished_campaign as finished_campaign,
)
from api.tests.test_campaign_call_dispatcher import (
    sessions as sessions,
)


async def make_uncertain(sessions, run_id, *, logs=None, **values):
    old = datetime.now(UTC) - timedelta(minutes=21)
    async with sessions() as session:
        await session.execute(
            update(WorkflowRunModel)
            .where(WorkflowRunModel.id == run_id)
            .values(
                {
                    "state": "initialized",
                    "is_completed": False,
                    "created_at": old,
                    "gathered_context": {
                        "call_initiation_uncertain": True,
                        "error": "provider request timed out",
                        "call_tags": ["retry"],
                    },
                    "logs": logs
                    if logs is not None
                    else {"campaign_dispatch": {"uncertain_at": old.isoformat()}},
                    **values,
                }
            )
        )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_stale_dispatch_becomes_terminal_and_cleanup_is_durable(
    finished_campaign, sessions, legacy
):
    s = finished_campaign
    await make_uncertain(sessions, s.run.id, logs={} if legacy else None)
    await db_client.update_queued_run(s.rows[0].id, state="processing")
    assert await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id) is None

    cutoff = datetime.now(UTC) - timedelta(minutes=20)
    pending = await db_client.recover_stale_campaign_dispatches(stale_before=cutoff)
    assert [row["workflow_run_id"] for row in pending] == [s.run.id]
    async with sessions() as session:
        run = await session.get(WorkflowRunModel, s.run.id)
        row = await session.get(QueuedRunModel, s.rows[0].id)
    assert (run.state, run.is_completed, row.state) == ("completed", True, "failed")
    assert run.gathered_context["error"] == "provider request timed out"
    assert run.gathered_context["call_tags"] == ["retry"]
    assert run.gathered_context["call_status"] == "failed"
    assert run.logs["campaign_dispatch"]["outcome"] == "stale"
    assert await db_client.complete_campaign_if_idle(s.campaign.id, s.org.id)

    # Cleanup is discoverable even after campaign completion and a new process.
    assert (
        await db_client.recover_stale_campaign_dispatches(stale_before=cutoff)
        == pending
    )
    await db_client.mark_campaign_dispatch_slot_reconciled(s.run.id, s.org.id + 1000000)
    assert (
        await db_client.recover_stale_campaign_dispatches(stale_before=cutoff)
        == pending
    )
    await db_client.mark_campaign_dispatch_slot_reconciled(s.run.id, s.org.id)
    assert await db_client.recover_stale_campaign_dispatches(stale_before=cutoff) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "fresh",
        "boundary",
        "legacy_fresh",
        "running",
        "terminal",
        "callback",
        "ordinary",
    ],
)
async def test_recovery_preserves_recent_and_provider_observed_calls(
    finished_campaign, sessions, case
):
    s = finished_campaign
    cutoff = datetime.now(UTC) - timedelta(minutes=20)
    values = {}
    if case in ("fresh", "boundary"):
        values["logs"] = {
            "campaign_dispatch": {
                "uncertain_at": (
                    datetime.now(UTC)
                    if case == "fresh"
                    else cutoff + timedelta(microseconds=1)
                ).isoformat()
            }
        }
    elif case == "legacy_fresh":
        values["logs"] = {}
        await db_client.update_queued_run(s.rows[0].id, processed_at=datetime.now(UTC))
    elif case == "running":
        values["state"] = "running"
    elif case == "terminal":
        values.update(state="completed", is_completed=True)
    elif case == "callback":
        values["logs"] = {"telephony_status_callbacks": [{"status": "ringing"}]}
    else:
        values["gathered_context"] = {}
    await make_uncertain(sessions, s.run.id, **values)
    assert await db_client.recover_stale_campaign_dispatches(stale_before=cutoff) == []


@pytest.mark.asyncio
async def test_recovery_skips_callback_writer_and_rechecks_committed_state(
    finished_campaign, sessions
):
    s = finished_campaign
    await make_uncertain(sessions, s.run.id)
    cutoff = datetime.now(UTC) - timedelta(minutes=20)
    async with sessions() as callback_session:
        await callback_session.execute(
            update(WorkflowRunModel)
            .where(WorkflowRunModel.id == s.run.id)
            .values(logs={"telephony_status_callbacks": [{"status": "answered"}]})
        )
        assert (
            await db_client.recover_stale_campaign_dispatches(stale_before=cutoff) == []
        )
        await callback_session.commit()
    assert await db_client.recover_stale_campaign_dispatches(stale_before=cutoff) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping_expired", [False, True])
async def test_monitor_recovers_without_callback_and_retries_redis_cleanup(
    finished_campaign, sessions, mapping_expired
):
    s = finished_campaign
    limiter = RateLimiter()
    redis = await limiter._get_redis()
    scope = f"campaign:{s.campaign.id}"
    slot = await limiter.try_acquire_concurrent_slot_details(
        s.org.id, 10, scope_key=scope, scope_max_concurrent=10
    )
    other_slot = await limiter.try_acquire_concurrent_slot_details(s.org.id, 10)
    mapping_key = f"workflow_slot_mapping:{s.run.id}"
    try:
        await limiter.store_workflow_slot_mapping_if_absent(
            s.run.id, s.org.id, slot.slot_id, scope_key=scope
        )
        if mapping_expired:
            await redis.expire(mapping_key, -1)
        await make_uncertain(
            sessions,
            s.run.id,
            logs={
                "campaign_dispatch": {
                    "uncertain_at": (
                        datetime.now(UTC) - timedelta(minutes=21)
                    ).isoformat(),
                    "slot_id": slot.slot_id,
                    "scope_key": scope,
                }
            },
        )
        with patch(
            "api.services.campaign.campaign_call_dispatcher.rate_limiter", limiter
        ):
            monitor = CampaignOrchestrator(AsyncMock())
            monitor.publisher.publish_campaign_completed = AsyncMock()
            with patch.object(
                limiter, "release_concurrent_slot", AsyncMock(return_value=None)
            ):
                await monitor._check_stale_campaigns()
            monitor.publisher.publish_campaign_completed.assert_awaited_once()
            assert (
                await redis.zscore(f"concurrent_calls:{scope}", slot.slot_id)
                is not None
            )
            async with sessions() as session:
                run = await session.get(WorkflowRunModel, s.run.id)
                assert run.logs["campaign_dispatch"]["slot_cleanup_pending"]

            # Fresh instances find cleanup for the now-completed campaign.
            await CampaignOrchestrator(AsyncMock())._check_stale_campaigns()
            await CampaignCallDispatcher().recover_stale_dispatches()
        assert not await redis.exists(mapping_key)
        assert await redis.zscore(f"concurrent_calls:{s.org.id}", slot.slot_id) is None
        assert await redis.zscore(f"concurrent_calls:{scope}", slot.slot_id) is None
        assert (
            await redis.zscore(FLEET_CONCURRENT_KEY, f"{s.org.id}:{slot.slot_id}")
            is None
        )
        assert (
            await redis.zscore(f"concurrent_calls:{s.org.id}", other_slot.slot_id)
            is not None
        )
        async with sessions() as session:
            run = await session.get(WorkflowRunModel, s.run.id)
            assert run.logs["campaign_dispatch"]["slot_cleanup_pending"] is False
    finally:
        await limiter.release_concurrent_slot(s.org.id, slot.slot_id, scope_key=scope)
        await limiter.release_concurrent_slot(s.org.id, other_slot.slot_id)
        await redis.delete(mapping_key)
        await limiter.close()
