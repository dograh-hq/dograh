"""
Tests for api.tasks.campaign_tasks failure handling.

Specifically: each kind of failure that pauses or fails a campaign should
write a specific, identifiable entry into the campaign log so operators
can tell at a glance why a campaign stopped.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign.errors import (
    ConcurrentSlotAcquisitionError,
    PhoneNumberPoolExhaustedError,
)
from api.tasks.campaign_tasks import process_campaign_batch


class TestProcessCampaignBatchFailureLogs:
    """``process_campaign_batch`` should log a *specific* event for each
    distinct failure mode, not collapse them all into a generic
    ``batch_failed`` entry."""

    @pytest.mark.asyncio
    async def test_phone_number_pool_exhausted_logs_specific_event(self):
        """When PhoneNumberPoolExhaustedError propagates from process_batch,
        the campaign log entry should use event='phone_number_pool_exhausted'
        with a clear message — not the generic 'batch_failed' bucket."""
        with (
            patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as mock_disp,
            patch("api.tasks.campaign_tasks.db_client") as mock_db,
            patch(
                "api.tasks.campaign_tasks.get_campaign_event_publisher"
            ) as mock_get_pub,
        ):
            mock_disp.process_batch = AsyncMock(
                side_effect=PhoneNumberPoolExhaustedError(organization_id=7)
            )
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_pub = AsyncMock()
            mock_get_pub.return_value = mock_pub

            with pytest.raises(PhoneNumberPoolExhaustedError):
                await process_campaign_batch({}, campaign_id=42)

            mock_db.update_campaign.assert_called_once_with(
                campaign_id=42, state="failed"
            )

            mock_db.append_campaign_log.assert_called_once()
            kwargs = mock_db.append_campaign_log.call_args.kwargs
            assert kwargs["campaign_id"] == 42
            assert kwargs["event"] == "phone_number_pool_exhausted"
            assert kwargs["level"] == "error"
            assert "phone number" in kwargs["message"].lower()
            assert kwargs["details"]["organization_id"] == 7

    @pytest.mark.asyncio
    async def test_concurrent_slot_timeout_still_logs_specific_event(self):
        """Regression guard: the existing ConcurrentSlotAcquisitionError branch
        should keep logging its specific reason."""
        with (
            patch("api.tasks.campaign_tasks.campaign_call_dispatcher") as mock_disp,
            patch("api.tasks.campaign_tasks.db_client") as mock_db,
            patch(
                "api.tasks.campaign_tasks.get_campaign_event_publisher"
            ) as mock_get_pub,
        ):
            mock_disp.process_batch = AsyncMock(
                side_effect=ConcurrentSlotAcquisitionError(
                    organization_id=7, campaign_id=42, wait_time=30.0
                )
            )
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_pub = AsyncMock()
            mock_get_pub.return_value = mock_pub

            with pytest.raises(ConcurrentSlotAcquisitionError):
                await process_campaign_batch({}, campaign_id=42)

            mock_db.append_campaign_log.assert_called_once()
            kwargs = mock_db.append_campaign_log.call_args.kwargs
            assert kwargs["event"] == "batch_failed"
            assert kwargs["details"]["reason"] == "concurrent_slot_timeout"
