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
        199, 10
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
