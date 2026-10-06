"""Capacity backpressure completes a batch without failing its campaign."""

from unittest.mock import AsyncMock, patch

import pytest

from api.tasks.campaign_tasks import process_campaign_batch


@pytest.mark.asyncio
@pytest.mark.parametrize("processed", [0, 5])
async def test_batch_completion_schedules_more_work_without_pool_retry_counters(
    processed,
):
    with (
        patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as dispatcher,
        patch("api.tasks.campaign_tasks.db_client") as db,
        patch(
            "api.tasks.campaign_tasks.get_campaign_event_publisher", AsyncMock()
        ) as get_pub,
    ):
        dispatcher.process_batch = AsyncMock(return_value=processed)
        await process_campaign_batch({}, 48)
        get_pub.return_value.publish_batch_completed.assert_awaited_once_with(
            campaign_id=48, processed_count=processed, failed_count=0, batch_size=20
        )
        db.update_campaign.assert_not_called()


@pytest.mark.asyncio
async def test_shared_setup_failure_is_logged():
    with (
        patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as dispatcher,
        patch("api.tasks.campaign_tasks.db_client") as db,
        patch(
            "api.tasks.campaign_tasks.get_campaign_event_publisher", AsyncMock()
        ) as get_pub,
    ):
        dispatcher.process_batch = AsyncMock(side_effect=ValueError("setup incomplete"))
        db.update_campaign = AsyncMock()
        db.append_campaign_log = AsyncMock()
        with pytest.raises(ValueError):
            await process_campaign_batch({}, 48)
        get_pub.return_value.publish_batch_failed.assert_awaited_once()
        assert db.append_campaign_log.await_args.kwargs["event"] == "batch_failed"
