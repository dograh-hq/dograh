"""
Tests for Campaign Circuit Breaker.

These tests verify:
1. Circuit breaker records call outcomes (success/failure)
2. Circuit breaker trips when failure rate exceeds threshold
3. Circuit breaker does NOT trip when below threshold or min_calls
4. Circuit breaker reset clears state
5. Integration: _process_status_update pauses campaign on circuit breaker trip
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =============================================================================
# Unit tests for CircuitBreaker class
# =============================================================================


class TestCircuitBreakerRecordOutcome:
    """Tests for recording call outcomes and trip detection."""

    @pytest.mark.asyncio
    async def test_no_trip_below_min_calls(self):
        """Circuit breaker should NOT trip when total calls < min_calls_in_window."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        # Mock Redis to simulate a window with 3 failures out of 3 total
        # (100% failure rate, but below min_calls=5)
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(
            return_value=[0, 3, 0, 3]  # [not_tripped, failures, successes, total]
        )
        cb.redis_client = mock_redis

        tripped, stats = await cb.record_call_outcome(campaign_id=1, is_failure=True)

        assert tripped is False
        assert stats is not None
        assert stats["failure_count"] == 3
        assert stats["success_count"] == 0

    @pytest.mark.asyncio
    async def test_trip_when_threshold_exceeded(self):
        """Circuit breaker should trip when failure rate >= threshold and total >= min_calls."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        # Mock Redis to simulate: 4 failures out of 6 total = 66% > 50% threshold
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(
            return_value=[1, 4, 2, 6]  # [tripped, failures, successes, total]
        )
        cb.redis_client = mock_redis

        tripped, stats = await cb.record_call_outcome(campaign_id=1, is_failure=True)

        assert tripped is True
        assert stats is not None
        assert stats["failure_rate"] == pytest.approx(4 / 6)
        assert stats["failure_count"] == 4
        assert stats["success_count"] == 2

    @pytest.mark.asyncio
    async def test_no_trip_below_threshold(self):
        """Circuit breaker should NOT trip when failure rate < threshold."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        # Mock Redis: 2 failures out of 8 total = 25% < 50% threshold
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(
            return_value=[0, 2, 6, 8]  # [not_tripped, failures, successes, total]
        )
        cb.redis_client = mock_redis

        tripped, stats = await cb.record_call_outcome(campaign_id=1, is_failure=False)

        assert tripped is False
        assert stats["failure_rate"] == pytest.approx(2 / 8)

    @pytest.mark.asyncio
    async def test_disabled_circuit_breaker(self):
        """Circuit breaker should not record or trip when disabled."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        mock_redis = AsyncMock()
        cb.redis_client = mock_redis

        tripped, stats = await cb.record_call_outcome(
            campaign_id=1,
            is_failure=True,
            config={"enabled": False},
        )

        assert tripped is False
        assert stats is None
        # Redis should not have been called
        mock_redis.eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_config_override(self):
        """Per-campaign config should override defaults."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        # With custom threshold of 0.8, 4/6 = 66% should NOT trip
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(
            return_value=[0, 4, 2, 6]  # Lua script respects the threshold we pass
        )
        cb.redis_client = mock_redis

        tripped, stats = await cb.record_call_outcome(
            campaign_id=1,
            is_failure=True,
            config={"failure_threshold": 0.8, "min_calls_in_window": 3},
        )

        assert tripped is False

    @pytest.mark.asyncio
    async def test_redis_error_fails_open(self):
        """On Redis error, circuit breaker should fail open (not trip)."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(side_effect=Exception("Redis connection lost"))
        cb.redis_client = mock_redis

        tripped, stats = await cb.record_call_outcome(campaign_id=1, is_failure=True)

        assert tripped is False
        assert stats is None


class TestCircuitBreakerIsOpen:
    """Tests for read-only circuit state check."""

    @pytest.mark.asyncio
    async def test_is_open_when_threshold_exceeded(self):
        """is_circuit_open should return True when failure rate exceeds threshold."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(
            return_value=[1, 5, 2, 7]  # [is_open, failures, successes, total]
        )
        cb.redis_client = mock_redis

        is_open, stats = await cb.is_circuit_open(campaign_id=1)

        assert is_open is True
        assert stats["failure_count"] == 5

    @pytest.mark.asyncio
    async def test_is_not_open_when_healthy(self):
        """is_circuit_open should return False when failure rate is low."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=[0, 1, 9, 10])
        cb.redis_client = mock_redis

        is_open, stats = await cb.is_circuit_open(campaign_id=1)

        assert is_open is False
        assert stats["failure_rate"] == pytest.approx(0.1)


class TestCircuitBreakerReset:
    """Tests for circuit breaker reset."""

    @pytest.mark.asyncio
    async def test_reset_deletes_redis_keys(self):
        """Reset should delete both failure and success keys for the campaign."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=2)
        cb.redis_client = mock_redis

        result = await cb.reset(campaign_id=42)

        assert result is True
        mock_redis.delete.assert_called_once_with("cb_failures:42", "cb_successes:42")

    @pytest.mark.asyncio
    async def test_reset_on_redis_error(self):
        """Reset should return False on Redis error."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("Redis down"))
        cb.redis_client = mock_redis

        result = await cb.reset(campaign_id=42)

        assert result is False


# =============================================================================
# Tests for record_and_evaluate (the high-level method on CircuitBreaker)
# =============================================================================


class TestRecordAndEvaluate:
    """Test circuit_breaker.record_and_evaluate which handles the full
    flow: record outcome, check trip, pause campaign, publish event."""

    @pytest.mark.asyncio
    async def test_trips_and_pauses_campaign(self):
        """When record_call_outcome returns tripped, campaign should be paused."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        mock_campaign = MagicMock()
        mock_campaign.id = 42
        mock_campaign.state = "running"
        mock_campaign.orchestrator_metadata = {}

        stats = {
            "failure_rate": 0.6,
            "failure_count": 6,
            "success_count": 4,
            "threshold": 0.5,
            "window_seconds": 120,
        }

        with (
            patch("api.services.campaign.circuit_breaker.db_client") as mock_db,
            patch(
                "api.services.campaign.circuit_breaker.get_campaign_event_publisher"
            ) as mock_get_publisher,
        ):
            mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
            mock_db.update_campaign = AsyncMock()

            mock_publisher = AsyncMock()
            mock_get_publisher.return_value = mock_publisher

            # Mock the internal record_call_outcome to return tripped
            cb.record_call_outcome = AsyncMock(return_value=(True, stats))

            await cb.record_and_evaluate(campaign_id=42, is_failure=True)

            # Verify campaign was paused
            mock_db.update_campaign.assert_called_once_with(
                campaign_id=42, state="paused"
            )

            # Verify event was published
            mock_publisher.publish_circuit_breaker_tripped.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_pause_when_not_tripped(self):
        """When record_call_outcome does NOT trip, campaign should not be paused."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        mock_campaign = MagicMock()
        mock_campaign.id = 42
        mock_campaign.state = "running"
        mock_campaign.orchestrator_metadata = {}

        with patch("api.services.campaign.circuit_breaker.db_client") as mock_db:
            mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)

            cb.record_call_outcome = AsyncMock(return_value=(False, None))

            await cb.record_and_evaluate(campaign_id=42, is_failure=False)

            mock_db.update_campaign.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_campaign_not_running(self):
        """Should skip when campaign is not in running state."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        mock_campaign = MagicMock()
        mock_campaign.id = 42
        mock_campaign.state = "paused"

        with patch("api.services.campaign.circuit_breaker.db_client") as mock_db:
            mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)

            cb.record_call_outcome = AsyncMock()

            await cb.record_and_evaluate(campaign_id=42, is_failure=True)

            # Should not even attempt to record
            cb.record_call_outcome.assert_not_called()

    @pytest.mark.asyncio
    async def test_reads_config_from_orchestrator_metadata(self):
        """Should pass circuit_breaker config from orchestrator_metadata."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        custom_config = {"failure_threshold": 0.3, "min_calls_in_window": 10}
        mock_campaign = MagicMock()
        mock_campaign.id = 42
        mock_campaign.state = "running"
        mock_campaign.orchestrator_metadata = {"circuit_breaker": custom_config}

        with patch("api.services.campaign.circuit_breaker.db_client") as mock_db:
            mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)

            cb.record_call_outcome = AsyncMock(return_value=(False, None))

            await cb.record_and_evaluate(campaign_id=42, is_failure=True)

            cb.record_call_outcome.assert_called_once_with(
                campaign_id=42,
                is_failure=True,
                config=custom_config,
            )

    @pytest.mark.asyncio
    async def test_error_is_swallowed(self):
        """Errors inside record_and_evaluate should be caught, not raised."""
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()

        with patch("api.services.campaign.circuit_breaker.db_client") as mock_db:
            mock_db.get_campaign_by_id = AsyncMock(side_effect=Exception("DB exploded"))

            # Should NOT raise
            await cb.record_and_evaluate(campaign_id=42, is_failure=True)


# =============================================================================
# Integration tests: _process_status_update calls circuit_breaker
# =============================================================================


class TestProcessStatusUpdateCircuitBreaker:
    """Test that _process_status_update calls circuit_breaker.record_and_evaluate
    for campaign calls."""

    @pytest.mark.asyncio
    async def test_failure_status_calls_record_and_evaluate(self):
        """When a campaign call fails, record_and_evaluate should be called
        with is_failure=True."""

        from api.routes.telephony import StatusCallbackRequest, _process_status_update

        mock_workflow_run = MagicMock()
        mock_workflow_run.id = 100
        mock_workflow_run.campaign_id = 42
        mock_workflow_run.queued_run_id = 10
        mock_workflow_run.state = "running"
        mock_workflow_run.logs = {"telephony_status_callbacks": []}
        mock_workflow_run.gathered_context = {}

        status = StatusCallbackRequest(
            call_id="call-123",
            status="failed",
        )

        with (
            patch("api.routes.telephony.db_client") as mock_db,
            patch("api.routes.telephony.campaign_call_dispatcher") as mock_dispatcher,
            patch("api.routes.telephony.circuit_breaker") as mock_cb,
            patch(
                "api.routes.telephony.get_campaign_event_publisher"
            ) as mock_get_publisher,
        ):
            mock_db.get_workflow_run_by_id = AsyncMock(return_value=mock_workflow_run)
            mock_db.update_workflow_run = AsyncMock()

            mock_dispatcher.release_call_slot = AsyncMock(return_value=True)
            mock_cb.record_and_evaluate = AsyncMock()

            mock_publisher = AsyncMock()
            mock_get_publisher.return_value = mock_publisher

            await _process_status_update(100, status)

            mock_cb.record_and_evaluate.assert_called_once_with(42, is_failure=True)

    @pytest.mark.asyncio
    async def test_success_status_calls_record_and_evaluate(self):
        """When a campaign call succeeds, record_and_evaluate should be called
        with is_failure=False."""

        from api.routes.telephony import StatusCallbackRequest, _process_status_update

        mock_workflow_run = MagicMock()
        mock_workflow_run.id = 100
        mock_workflow_run.campaign_id = 42
        mock_workflow_run.state = "running"
        mock_workflow_run.logs = {"telephony_status_callbacks": []}
        mock_workflow_run.gathered_context = {}

        status = StatusCallbackRequest(
            call_id="call-456",
            status="completed",
        )

        with (
            patch("api.routes.telephony.db_client") as mock_db,
            patch("api.routes.telephony.campaign_call_dispatcher") as mock_dispatcher,
            patch("api.routes.telephony.circuit_breaker") as mock_cb,
        ):
            mock_db.get_workflow_run_by_id = AsyncMock(return_value=mock_workflow_run)
            mock_db.update_workflow_run = AsyncMock()

            mock_dispatcher.release_call_slot = AsyncMock(return_value=True)
            mock_cb.record_and_evaluate = AsyncMock()

            await _process_status_update(100, status)

            mock_cb.record_and_evaluate.assert_called_once_with(42, is_failure=False)

    @pytest.mark.asyncio
    async def test_non_campaign_call_skips_circuit_breaker(self):
        """Calls without campaign_id should not interact with circuit breaker."""

        from api.routes.telephony import StatusCallbackRequest, _process_status_update

        mock_workflow_run = MagicMock()
        mock_workflow_run.id = 100
        mock_workflow_run.campaign_id = None  # Not a campaign call
        mock_workflow_run.state = "running"
        mock_workflow_run.logs = {"telephony_status_callbacks": []}
        mock_workflow_run.gathered_context = {}

        status = StatusCallbackRequest(
            call_id="call-789",
            status="failed",
        )

        with (
            patch("api.routes.telephony.db_client") as mock_db,
            patch("api.routes.telephony.circuit_breaker") as mock_cb,
        ):
            mock_db.get_workflow_run_by_id = AsyncMock(return_value=mock_workflow_run)
            mock_db.update_workflow_run = AsyncMock()

            await _process_status_update(100, status)

            # Circuit breaker should NOT be called for non-campaign calls
            mock_cb.record_and_evaluate.assert_not_called()


# =============================================================================
# Integration test: resume_campaign resets circuit breaker
# =============================================================================


class TestResumeCampaignResetsCircuitBreaker:
    """Test that resuming a campaign resets the circuit breaker."""

    @pytest.mark.asyncio
    async def test_resume_resets_circuit_breaker(self):
        """Resuming a paused campaign should reset the circuit breaker state."""
        from api.services.campaign.runner import CampaignRunnerService

        mock_campaign = MagicMock()
        mock_campaign.id = 42
        mock_campaign.state = "paused"

        with (
            patch("api.services.campaign.runner.db_client") as mock_db,
            patch("api.services.campaign.runner.circuit_breaker") as mock_cb,
        ):
            mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
            mock_db.update_campaign = AsyncMock()
            mock_cb.reset = AsyncMock(return_value=True)

            runner = CampaignRunnerService()
            await runner.resume_campaign(42)

            # Verify circuit breaker was reset
            mock_cb.reset.assert_called_once_with(42)

            # Verify campaign state was updated
            mock_db.update_campaign.assert_called_once_with(
                campaign_id=42, state="running"
            )
