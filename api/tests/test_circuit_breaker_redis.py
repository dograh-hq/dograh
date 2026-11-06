"""Exercise the actual outcome Lua scripts against the configured test Redis."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.campaign import circuit_breaker as module


@pytest.fixture
async def pipeline_breaker(monkeypatch):
    breaker = module.CircuitBreaker()
    other_worker = module.CircuitBreaker()
    campaign_id = -uuid.uuid4().int
    clock = SimpleNamespace(now=1_700_000_000.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock.now))
    redis = await breaker._get_redis()
    await redis.ping()
    try:
        yield SimpleNamespace(
            breaker=breaker,
            other_worker=other_worker,
            campaign_id=campaign_id,
            clock=clock,
            redis=redis,
        )
    finally:
        await redis.delete(
            *breaker._keys(campaign_id),
            breaker._recent_failures_key(campaign_id),
        )
        await breaker.close()
        await other_worker.close()


@pytest.mark.asyncio
async def test_distinct_calls_at_same_timestamp_count_and_trip(pipeline_breaker):
    s = pipeline_breaker
    # All five completions have exactly the same timestamp. Timestamp members
    # used to collapse these into one success and one failure.
    for run_id, failed in enumerate([False, False, True, True, True]):
        tripped, stats = await s.breaker.record_call_outcome(
            s.campaign_id,
            failed,
            workflow_run_id=run_id,
            reason="pipeline_error" if failed else "user_hangup",
        )
        assert tripped is (run_id == 4)

    assert stats["failure_count"] == 3
    assert stats["success_count"] == 2
    assert stats["failure_rate"] == 0.6
    assert (await s.breaker.is_circuit_open(s.campaign_id))[0] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_duplicate_outcomes_across_workers_count_once(pipeline_breaker, failed):
    s = pipeline_breaker
    async with asyncio.timeout(10):
        await asyncio.gather(
            *(
                worker.record_call_outcome(
                    s.campaign_id,
                    failed,
                    workflow_run_id=88,
                    reason="pipeline_error" if failed else "user_hangup",
                )
                for worker in [s.breaker, s.other_worker] * 10
            )
        )
    # A contradictory repeat must not put the same call in the other set.
    s.clock.now += 10
    tripped, stats = await s.other_worker.record_call_outcome(
        s.campaign_id, not failed, workflow_run_id=88
    )

    assert not tripped  # Twenty deliveries still fall below min_calls=5.
    assert stats["failure_count"] == int(failed)
    assert stats["success_count"] == int(not failed)
    recent = await s.breaker._get_recent_failures(s.campaign_id)
    assert len(recent) == int(failed)
    if failed:
        assert recent[0]["workflow_run_id"] == 88
        assert recent[0]["reason"] == "pipeline_error"


@pytest.mark.asyncio
async def test_duplicate_does_not_extend_the_sliding_window(pipeline_breaker):
    s = pipeline_breaker
    config = {"window_seconds": 120, "min_calls_in_window": 1}
    assert (
        await s.breaker.record_call_outcome(
            s.campaign_id, True, config, workflow_run_id=88
        )
    )[0]
    s.clock.now += 110
    await s.other_worker.record_call_outcome(
        s.campaign_id, True, config, workflow_run_id=88
    )
    s.clock.now += 11
    tripped, stats = await s.breaker.is_circuit_open(s.campaign_id, config)
    assert not tripped
    assert stats["failure_count"] == stats["success_count"] == 0


@pytest.mark.asyncio
async def test_recent_failures_are_bounded_unique_and_use_campaign_ttl(
    pipeline_breaker,
):
    s = pipeline_breaker
    config = {"window_seconds": 180, "min_calls_in_window": 100}
    for run_id in range(module.MAX_RECENT_FAILURES + 5):
        await s.breaker.record_call_outcome(
            s.campaign_id, True, config, workflow_run_id=run_id, reason="pipeline_error"
        )
    for _ in range(3):
        await s.other_worker.record_call_outcome(
            s.campaign_id, True, config, workflow_run_id=24, reason="pipeline_error"
        )
    recent = await s.breaker._get_recent_failures(s.campaign_id)
    assert [r["workflow_run_id"] for r in recent] == list(range(24, 4, -1))
    assert await s.redis.ttl(s.breaker._recent_failures_key(s.campaign_id)) > 230


@pytest.mark.asyncio
async def test_successes_and_duplicates_do_not_extend_failure_diagnostics_ttl(
    pipeline_breaker,
):
    s = pipeline_breaker
    await s.breaker.record_call_outcome(s.campaign_id, True, workflow_run_id=88)
    key = s.breaker._recent_failures_key(s.campaign_id)
    # Shorten the real Redis TTL without sleeping through an entire window.
    await s.redis.expire(key, 10)
    await s.breaker.record_call_outcome(s.campaign_id, False, workflow_run_id=89)
    await s.other_worker.record_call_outcome(s.campaign_id, True, workflow_run_id=88)
    assert 0 < await s.redis.ttl(key) <= 10
    await s.breaker.record_call_outcome(s.campaign_id, True, workflow_run_id=90)
    assert await s.redis.ttl(key) > 170


@pytest.mark.asyncio
async def test_trip_diagnostics_exclude_failures_outside_campaign_window(
    pipeline_breaker,
):
    s = pipeline_breaker
    await s.breaker.record_call_outcome(s.campaign_id, True, workflow_run_id=88)
    s.clock.now += 121
    await s.breaker.record_call_outcome(s.campaign_id, True, workflow_run_id=89)
    recent = await s.breaker._get_recent_failures(
        s.campaign_id, since=s.clock.now - 120
    )
    assert [entry["workflow_run_id"] for entry in recent] == [89]


@pytest.mark.asyncio
async def test_reset_clears_counts_and_failure_diagnostics(pipeline_breaker):
    s = pipeline_breaker
    await s.breaker.record_call_outcome(s.campaign_id, True, workflow_run_id=88)
    await s.breaker.record_call_outcome(s.campaign_id, False, workflow_run_id=89)
    assert await s.breaker.reset(s.campaign_id)
    tripped, stats = await s.breaker.is_circuit_open(s.campaign_id)
    assert not tripped
    assert stats["failure_count"] == stats["success_count"] == 0
    assert await s.breaker._get_recent_failures(s.campaign_id) == []


@pytest.mark.asyncio
async def test_pipeline_failures_pause_campaign_despite_carrier_successes(
    pipeline_breaker, monkeypatch
):
    s = pipeline_breaker
    # Exercise a 100% failure threshold, with enough legacy "successes" (busy /
    # no-answer) to prevent the old breaker from ever opening.
    legacy_key = f"cb_successes:{s.campaign_id}"
    await s.redis.zadd(legacy_key, {str(i): s.clock.now for i in range(50)})
    campaign = SimpleNamespace(
        state="running",
        orchestrator_metadata={
            "circuit_breaker": {
                "failure_threshold": 1.0,
                "min_calls_in_window": 5,
            }
        },
    )
    db = SimpleNamespace(
        get_campaign_by_id=AsyncMock(return_value=campaign),
        update_campaign=AsyncMock(),
        append_campaign_log=AsyncMock(),
    )
    publisher = SimpleNamespace(publish_circuit_breaker_tripped=AsyncMock())
    monkeypatch.setattr(module, "db_client", db)
    monkeypatch.setattr(
        module, "get_campaign_event_publisher", AsyncMock(return_value=publisher)
    )
    try:
        for run_id in range(5):
            await s.breaker.record_and_evaluate(
                s.campaign_id,
                True,
                workflow_run_id=run_id,
                reason="pipeline_error",
            )
        db.update_campaign.assert_awaited_once_with(
            campaign_id=s.campaign_id, state="paused"
        )
        details = db.append_campaign_log.await_args.kwargs["details"]
        assert details["failure_rate"] == 1.0
        assert details["failure_count"] == 5
        assert details["success_count"] == 0
        assert len(details["recent_failures"]) == 5
        publisher.publish_circuit_breaker_tripped.assert_awaited_once()
    finally:
        await s.redis.delete(legacy_key)
