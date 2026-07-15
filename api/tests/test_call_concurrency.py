from unittest.mock import AsyncMock, patch

import pytest

from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    CallConcurrencyService,
)
from api.services.campaign.rate_limiter import ConcurrentSlotAcquisition


@pytest.mark.asyncio
async def test_acquire_org_slot_logs_post_acquire_count_and_limit():
    service = CallConcurrencyService()

    with (
        patch("api.services.call_concurrency.db_client") as mock_db,
        patch("api.services.call_concurrency.rate_limiter") as mock_rate_limiter,
        patch("api.services.call_concurrency.logger") as mock_logger,
    ):
        mock_db.get_configuration = AsyncMock(return_value=None)
        mock_rate_limiter.try_acquire_concurrent_slot_details = AsyncMock(
            return_value=ConcurrentSlotAcquisition(
                slot_id="slot-123",
                active_count=7,
            )
        )

        slot = await service.acquire_org_slot(199, source="test_source")

    assert slot.organization_id == 199
    assert slot.slot_id == "slot-123"
    assert slot.max_concurrent == 10
    assert slot.source == "test_source"
    mock_rate_limiter.try_acquire_concurrent_slot_details.assert_awaited_once_with(
        199, 10, scope_key=None, scope_max_concurrent=None
    )
    mock_logger.info.assert_called_once()
    log_message = mock_logger.info.call_args.args[0]
    assert "org 199" in log_message
    assert "source=test_source" in log_message
    assert "active_calls=7/10" in log_message
    assert "slot_id=slot-123" in log_message


@pytest.mark.asyncio
async def test_acquire_org_slot_logs_warning_when_limit_reached():
    service = CallConcurrencyService()

    with (
        patch("api.services.call_concurrency.db_client") as mock_db,
        patch("api.services.call_concurrency.rate_limiter") as mock_rate_limiter,
        patch("api.services.call_concurrency.logger") as mock_logger,
    ):
        mock_db.get_configuration = AsyncMock(return_value=None)
        mock_rate_limiter.try_acquire_concurrent_slot_details = AsyncMock(
            return_value=None
        )
        mock_rate_limiter.get_concurrent_count = AsyncMock(return_value=12)

        with pytest.raises(CallConcurrencyLimitError):
            await service.acquire_org_slot(199, source="test_source", timeout=0)

    mock_rate_limiter.get_concurrent_count.assert_awaited_once_with(199)
    mock_logger.warning.assert_called_once()
    log_message = mock_logger.warning.call_args.args[0]
    assert "Concurrent call limit reached for org 199" in log_message
    assert "source=test_source" in log_message
    assert "active_calls=12/10" in log_message


@pytest.mark.asyncio
async def test_acquire_org_slot_fires_usage_event_per_org_member_when_limit_reached():
    """Mirrors the MPS org-event convention: one event per org member with the
    member's provider_id as distinct_id, event_source property, no $groups."""
    from types import SimpleNamespace

    from api.enums import PostHogEvent

    service = CallConcurrencyService()
    members = [
        SimpleNamespace(provider_id="user-a"),
        SimpleNamespace(provider_id="user-b"),
    ]

    with (
        patch("api.services.call_concurrency.db_client") as mock_db,
        patch("api.services.call_concurrency.rate_limiter") as mock_rate_limiter,
        patch("api.services.call_concurrency.capture_event") as mock_capture,
    ):
        mock_db.get_configuration = AsyncMock(return_value=None)
        mock_db.get_organization_users = AsyncMock(return_value=members)
        mock_rate_limiter.try_acquire_concurrent_slot_details = AsyncMock(
            return_value=None
        )
        mock_rate_limiter.get_concurrent_count = AsyncMock(return_value=10)

        with pytest.raises(CallConcurrencyLimitError):
            await service.acquire_org_slot(199, source="webrtc", timeout=0)

    mock_db.get_organization_users.assert_awaited_once_with(199)
    assert mock_capture.call_count == 2
    distinct_ids = [c.kwargs["distinct_id"] for c in mock_capture.call_args_list]
    assert distinct_ids == ["user-a", "user-b"]
    for call in mock_capture.call_args_list:
        kwargs = call.kwargs
        assert kwargs["event"] == PostHogEvent.USAGE_CONCURRENT_CALL_LIMIT_REACHED
        assert "groups" not in kwargs
        assert kwargs["properties"]["event_source"] == "dograh"
        assert kwargs["properties"]["organization_id"] == 199
        assert kwargs["properties"]["source"] == "webrtc"
        assert kwargs["properties"]["active_calls"] == 10
        assert kwargs["properties"]["max_concurrent"] == 10
        assert "scope_key" not in kwargs["properties"]


@pytest.mark.asyncio
async def test_acquire_org_slot_passes_scope_to_rate_limiter():
    service = CallConcurrencyService()

    with (
        patch("api.services.call_concurrency.db_client") as mock_db,
        patch("api.services.call_concurrency.rate_limiter") as mock_rate_limiter,
    ):
        mock_db.get_configuration = AsyncMock(return_value=None)
        mock_rate_limiter.try_acquire_concurrent_slot_details = AsyncMock(
            return_value=ConcurrentSlotAcquisition(slot_id="slot-123", active_count=1)
        )
        mock_rate_limiter.store_workflow_slot_mapping_if_absent = AsyncMock(
            return_value=True
        )

        slot = await service.acquire_org_slot(
            199,
            source="campaign:42",
            scope_key="campaign:42",
            scope_max_concurrent=3,
        )
        await service.bind_workflow_run(slot, 501)

    assert slot.scope_key == "campaign:42"
    mock_rate_limiter.try_acquire_concurrent_slot_details.assert_awaited_once_with(
        199, 10, scope_key="campaign:42", scope_max_concurrent=3
    )
    mock_rate_limiter.store_workflow_slot_mapping_if_absent.assert_awaited_once_with(
        501, 199, "slot-123", scope_key="campaign:42"
    )


@pytest.mark.asyncio
async def test_release_workflow_run_slot_keeps_mapping_on_redis_error():
    service = CallConcurrencyService()

    with patch("api.services.call_concurrency.rate_limiter") as mock_rate_limiter:
        mock_rate_limiter.get_workflow_slot_mapping = AsyncMock(
            return_value=(11, "slot-1", None)
        )
        # None = Redis error during release (vs False = slot already gone)
        mock_rate_limiter.release_concurrent_slot = AsyncMock(return_value=None)
        mock_rate_limiter.delete_workflow_slot_mapping = AsyncMock()

        released = await service.release_workflow_run_slot(501)

    assert released is False
    mock_rate_limiter.release_concurrent_slot.assert_awaited_once_with(
        11, "slot-1", scope_key=None
    )
    mock_rate_limiter.delete_workflow_slot_mapping.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_workflow_run_slot_deletes_mapping_when_slot_already_gone():
    service = CallConcurrencyService()

    with patch("api.services.call_concurrency.rate_limiter") as mock_rate_limiter:
        mock_rate_limiter.get_workflow_slot_mapping = AsyncMock(
            return_value=(11, "slot-1", "campaign:42")
        )
        mock_rate_limiter.release_concurrent_slot = AsyncMock(return_value=False)
        mock_rate_limiter.delete_workflow_slot_mapping = AsyncMock(return_value=True)

        released = await service.release_workflow_run_slot(501)

    assert released is False
    mock_rate_limiter.release_concurrent_slot.assert_awaited_once_with(
        11, "slot-1", scope_key="campaign:42"
    )
    mock_rate_limiter.delete_workflow_slot_mapping.assert_awaited_once_with(501)


@pytest.mark.asyncio
async def test_unregister_active_call_never_raises():
    service = CallConcurrencyService()

    with patch("api.services.call_concurrency.rate_limiter") as mock_rate_limiter:
        mock_rate_limiter.get_workflow_slot_mapping = AsyncMock(
            side_effect=RuntimeError("redis down")
        )

        released = await service.unregister_active_call(501)

    assert released is False


# ---------------------------------------------------------------------------
# Redis integration tests for scoped (campaign-level) slot acquisition
# ---------------------------------------------------------------------------

import os  # noqa: E402
import uuid  # noqa: E402

from api.services.campaign.rate_limiter import RateLimiter  # noqa: E402

requires_redis = pytest.mark.skipif(
    "REDIS_URL" not in os.environ,
    reason="Requires Redis (set REDIS_URL via .env.test)",
)


def _unique_org_id() -> int:
    return uuid.uuid4().int % 10_000_000


@requires_redis
@pytest.mark.asyncio
async def test_scoped_acquisition_enforces_scope_limit_independently_of_org():
    """A campaign scope caps its own calls without measuring — or being
    starved by — other calls in the same org counter."""
    rl = RateLimiter()
    org_id = _unique_org_id()
    scope = f"campaign:{org_id}"
    org_key = f"concurrent_calls:{org_id}"
    scope_key_full = f"concurrent_calls:{scope}"
    redis_client = await rl._get_redis()

    try:
        # Unscoped (e.g. WebRTC) calls fill part of the org counter.
        for _ in range(3):
            assert await rl.try_acquire_concurrent_slot_details(org_id, 10)

        # Scope limit 2: two scoped acquisitions succeed...
        first = await rl.try_acquire_concurrent_slot_details(
            org_id, 10, scope_key=scope, scope_max_concurrent=2
        )
        second = await rl.try_acquire_concurrent_slot_details(
            org_id, 10, scope_key=scope, scope_max_concurrent=2
        )
        assert first and second

        # ...the third is rejected by the scope even though the org has room.
        third = await rl.try_acquire_concurrent_slot_details(
            org_id, 10, scope_key=scope, scope_max_concurrent=2
        )
        assert third is None

        # Unscoped calls are unaffected by the scope being full.
        assert await rl.try_acquire_concurrent_slot_details(org_id, 10)

        # Releasing with the scope key frees both counters.
        released = await rl.release_concurrent_slot(
            org_id, first.slot_id, scope_key=scope
        )
        assert released is True
        assert await redis_client.zscore(org_key, first.slot_id) is None
        assert await redis_client.zscore(scope_key_full, first.slot_id) is None

        # And the scope accepts a new call again.
        assert await rl.try_acquire_concurrent_slot_details(
            org_id, 10, scope_key=scope, scope_max_concurrent=2
        )
    finally:
        await redis_client.delete(org_key, scope_key_full)
        await rl.close()


@requires_redis
@pytest.mark.asyncio
async def test_org_limit_still_binds_scoped_acquisition():
    rl = RateLimiter()
    org_id = _unique_org_id()
    scope = f"campaign:{org_id}"
    org_key = f"concurrent_calls:{org_id}"
    scope_key_full = f"concurrent_calls:{scope}"
    redis_client = await rl._get_redis()

    try:
        assert await rl.try_acquire_concurrent_slot_details(org_id, 1)

        # Org counter is full, so the scoped acquire fails and must not
        # leave a phantom entry in the scope counter.
        rejected = await rl.try_acquire_concurrent_slot_details(
            org_id, 1, scope_key=scope, scope_max_concurrent=5
        )
        assert rejected is None
        assert await redis_client.zcard(scope_key_full) == 0
    finally:
        await redis_client.delete(org_key, scope_key_full)
        await rl.close()


@requires_redis
@pytest.mark.asyncio
async def test_workflow_slot_mapping_round_trips_scope_key():
    rl = RateLimiter()
    run_id = _unique_org_id()
    mapping_key = f"workflow_slot_mapping:{run_id}"
    redis_client = await rl._get_redis()

    try:
        stored = await rl.store_workflow_slot_mapping_if_absent(
            run_id, 11, "slot-1", scope_key="campaign:42"
        )
        assert stored is True
        assert await rl.get_workflow_slot_mapping(run_id) == (
            11,
            "slot-1",
            "campaign:42",
        )

        # Unscoped mappings surface scope_key=None.
        run_id_2 = _unique_org_id()
        try:
            await rl.store_workflow_slot_mapping_if_absent(run_id_2, 11, "slot-2")
            assert await rl.get_workflow_slot_mapping(run_id_2) == (
                11,
                "slot-2",
                None,
            )
        finally:
            await redis_client.delete(f"workflow_slot_mapping:{run_id_2}")
    finally:
        await redis_client.delete(mapping_key)
        await rl.close()
