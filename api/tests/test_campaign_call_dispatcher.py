"""
Tests for CampaignCallDispatcher.process_batch method.

These tests verify:
1. Basic batch processing functionality
2. Thread-safety via SELECT FOR UPDATE SKIP LOCKED
3. Race condition handling when multiple workers process concurrently
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, text

from api.db.models import (
    CampaignModel,
    OrganizationModel,
    QueuedRunModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher

# =============================================================================
# Test-specific fixtures
# =============================================================================


@dataclass
class CampaignTestData:
    """Container for campaign test data IDs"""

    organization_id: int
    user_id: int
    workflow_id: int
    campaign_id: int
    queued_run_ids: List[int]


@pytest.fixture
async def campaign_test_data(db_session_factory) -> CampaignTestData:
    """
    Create test data for campaign processing tests.

    Creates:
    - Organization
    - User
    - Workflow
    - Campaign (in 'running' state)
    - 10 QueuedRuns (in 'queued' state)
    """
    async with db_session_factory() as session:
        # Create organization
        org = OrganizationModel(
            provider_id=f"test-org-{uuid.uuid4().hex[:8]}",
        )
        session.add(org)
        await session.flush()

        # Create user
        user = UserModel(
            provider_id=f"test-user-{uuid.uuid4().hex[:8]}",
            selected_organization_id=org.id,
        )
        session.add(user)
        await session.flush()

        # Create workflow
        workflow = WorkflowModel(
            name=f"test-workflow-{uuid.uuid4().hex[:8]}",
            user_id=user.id,
            organization_id=org.id,
            workflow_definition={
                "nodes": [
                    {
                        "id": "1",
                        "type": "startCall",
                        "position": {"x": 0, "y": 0},
                        "data": {"name": "Start", "prompt": "Hello"},
                    }
                ],
                "edges": [],
            },
            template_context_variables={},
        )
        session.add(workflow)
        await session.flush()

        # Create campaign
        campaign = CampaignModel(
            name=f"test-campaign-{uuid.uuid4().hex[:8]}",
            organization_id=org.id,
            workflow_id=workflow.id,
            created_by=user.id,
            source_type="test",
            source_id="test-source",
            state="running",
            rate_limit_per_second=100,  # High limit to avoid rate limiting in tests
        )
        session.add(campaign)
        await session.flush()

        # Create queued runs
        queued_run_ids = []
        for i in range(10):
            queued_run = QueuedRunModel(
                campaign_id=campaign.id,
                source_uuid=f"test-uuid-{i}",
                context_variables={"phone_number": f"+1555000{i:04d}"},
                state="queued",
            )
            session.add(queued_run)
            await session.flush()
            queued_run_ids.append(queued_run.id)

        await session.commit()

        test_data = CampaignTestData(
            organization_id=org.id,
            user_id=user.id,
            workflow_id=workflow.id,
            campaign_id=campaign.id,
            queued_run_ids=queued_run_ids,
        )

        yield test_data

        # Cleanup
        async with db_session_factory() as cleanup_session:
            # Delete in reverse order of dependencies
            await cleanup_session.execute(
                delete(QueuedRunModel).where(QueuedRunModel.campaign_id == campaign.id)
            )
            await cleanup_session.execute(
                delete(WorkflowRunModel).where(
                    WorkflowRunModel.campaign_id == campaign.id
                )
            )
            await cleanup_session.execute(
                delete(CampaignModel).where(CampaignModel.id == campaign.id)
            )
            await cleanup_session.execute(
                delete(WorkflowModel).where(WorkflowModel.id == workflow.id)
            )
            await cleanup_session.execute(
                delete(UserModel).where(UserModel.id == user.id)
            )
            await cleanup_session.execute(
                delete(OrganizationModel).where(OrganizationModel.id == org.id)
            )
            await cleanup_session.commit()


@pytest.fixture
def mock_dispatch_call():
    """Mock dispatch_call to track which runs were processed."""
    processed_runs = []

    async def mock_dispatch(queued_run, campaign, slot_id):
        # Simulate some processing time
        await asyncio.sleep(0.01)
        processed_runs.append(queued_run.id)
        # Return a mock workflow run
        mock_run = MagicMock()
        mock_run.id = len(processed_runs)
        return mock_run

    return mock_dispatch, processed_runs


@pytest.fixture
def mock_rate_limiter():
    """Mock rate limiter to always allow calls."""

    async def mock_acquire_token(*args, **kwargs):
        return True

    async def mock_try_acquire_slot(*args, **kwargs):
        return f"slot-{uuid.uuid4().hex[:8]}"

    async def mock_release_slot(*args, **kwargs):
        return True

    async def mock_store_mapping(*args, **kwargs):
        pass

    async def mock_get_mapping(*args, **kwargs):
        return None

    async def mock_delete_mapping(*args, **kwargs):
        pass

    return {
        "acquire_token": mock_acquire_token,
        "try_acquire_concurrent_slot": mock_try_acquire_slot,
        "release_concurrent_slot": mock_release_slot,
        "store_workflow_slot_mapping": mock_store_mapping,
        "get_workflow_slot_mapping": mock_get_mapping,
        "delete_workflow_slot_mapping": mock_delete_mapping,
    }


# =============================================================================
# Tests
# =============================================================================


class TestProcessBatchBasic:
    """Basic tests for process_batch functionality."""

    @pytest.mark.asyncio
    async def test_process_batch_processes_queued_runs(
        self, campaign_test_data, mock_dispatch_call, mock_rate_limiter
    ):
        """Test that process_batch processes queued runs and marks them as processed."""
        mock_dispatch, processed_runs = mock_dispatch_call

        with patch(
            "api.services.campaign.campaign_call_dispatcher.rate_limiter"
        ) as mock_rl:
            # Setup rate limiter mocks
            mock_rl.acquire_token = AsyncMock(
                side_effect=mock_rate_limiter["acquire_token"]
            )
            mock_rl.try_acquire_concurrent_slot = AsyncMock(
                side_effect=mock_rate_limiter["try_acquire_concurrent_slot"]
            )
            mock_rl.release_concurrent_slot = AsyncMock(
                side_effect=mock_rate_limiter["release_concurrent_slot"]
            )
            mock_rl.store_workflow_slot_mapping = AsyncMock(
                side_effect=mock_rate_limiter["store_workflow_slot_mapping"]
            )
            mock_rl.get_workflow_slot_mapping = AsyncMock(
                side_effect=mock_rate_limiter["get_workflow_slot_mapping"]
            )
            mock_rl.delete_workflow_slot_mapping = AsyncMock(
                side_effect=mock_rate_limiter["delete_workflow_slot_mapping"]
            )

            dispatcher = CampaignCallDispatcher()

            # Mock dispatch_call
            with patch.object(dispatcher, "dispatch_call", side_effect=mock_dispatch):
                # Process batch of 5
                processed_count = await dispatcher.process_batch(
                    campaign_id=campaign_test_data.campaign_id, batch_size=5
                )

            assert processed_count == 5
            assert len(processed_runs) == 5


class TestProcessBatchConcurrency:
    """Tests for concurrent batch processing and database locking."""

    @pytest.mark.asyncio
    async def test_concurrent_process_batch_no_duplicate_processing(
        self,
        campaign_test_data,
        mock_dispatch_call,
        mock_rate_limiter,
        db_session_factory,
    ):
        """
        Test that two concurrent process_batch calls don't process the same runs.

        This verifies the SELECT FOR UPDATE SKIP LOCKED mechanism works correctly.
        """
        mock_dispatch, processed_runs = mock_dispatch_call

        # Reset queued runs to 'queued' state for this test
        async with db_session_factory() as session:
            await session.execute(
                text(
                    "UPDATE queued_runs SET state = 'queued' WHERE campaign_id = :campaign_id"
                ),
                {"campaign_id": campaign_test_data.campaign_id},
            )
            await session.commit()

        async def run_process_batch():
            """Helper to run process_batch with mocked dependencies."""
            with patch(
                "api.services.campaign.campaign_call_dispatcher.rate_limiter"
            ) as mock_rl:
                mock_rl.acquire_token = AsyncMock(
                    side_effect=mock_rate_limiter["acquire_token"]
                )
                mock_rl.try_acquire_concurrent_slot = AsyncMock(
                    side_effect=mock_rate_limiter["try_acquire_concurrent_slot"]
                )
                mock_rl.release_concurrent_slot = AsyncMock(
                    side_effect=mock_rate_limiter["release_concurrent_slot"]
                )
                mock_rl.store_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["store_workflow_slot_mapping"]
                )
                mock_rl.get_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["get_workflow_slot_mapping"]
                )
                mock_rl.delete_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["delete_workflow_slot_mapping"]
                )

                dispatcher = CampaignCallDispatcher()

                with patch.object(
                    dispatcher, "dispatch_call", side_effect=mock_dispatch
                ):
                    return await dispatcher.process_batch(
                        campaign_id=campaign_test_data.campaign_id, batch_size=5
                    )

        # Run two process_batch calls concurrently
        results = await asyncio.gather(
            run_process_batch(),
            run_process_batch(),
        )

        # Total processed should be 10 (all queued runs)
        total_processed = sum(results)
        assert total_processed == 10, f"Expected 10 total, got {total_processed}"

        # Each run should be processed exactly once (no duplicates)
        assert len(processed_runs) == 10, f"Expected 10 runs, got {len(processed_runs)}"
        assert len(set(processed_runs)) == 10, "Duplicate runs were processed!"

    @pytest.mark.asyncio
    async def test_concurrent_process_batch_with_different_batch_sizes(
        self,
        campaign_test_data,
        mock_dispatch_call,
        mock_rate_limiter,
        db_session_factory,
    ):
        """
        Test concurrent processing with different batch sizes.

        Worker 1 requests 3 runs, Worker 2 requests 7 runs.
        Total should still be 10 with no duplicates.
        """
        mock_dispatch, processed_runs = mock_dispatch_call

        # Reset queued runs to 'queued' state
        async with db_session_factory() as session:
            await session.execute(
                text(
                    "UPDATE queued_runs SET state = 'queued' WHERE campaign_id = :campaign_id"
                ),
                {"campaign_id": campaign_test_data.campaign_id},
            )
            await session.commit()

        async def run_process_batch(batch_size: int):
            with patch(
                "api.services.campaign.campaign_call_dispatcher.rate_limiter"
            ) as mock_rl:
                mock_rl.acquire_token = AsyncMock(
                    side_effect=mock_rate_limiter["acquire_token"]
                )
                mock_rl.try_acquire_concurrent_slot = AsyncMock(
                    side_effect=mock_rate_limiter["try_acquire_concurrent_slot"]
                )
                mock_rl.release_concurrent_slot = AsyncMock(
                    side_effect=mock_rate_limiter["release_concurrent_slot"]
                )
                mock_rl.store_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["store_workflow_slot_mapping"]
                )
                mock_rl.get_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["get_workflow_slot_mapping"]
                )
                mock_rl.delete_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["delete_workflow_slot_mapping"]
                )

                dispatcher = CampaignCallDispatcher()

                with patch.object(
                    dispatcher, "dispatch_call", side_effect=mock_dispatch
                ):
                    return await dispatcher.process_batch(
                        campaign_id=campaign_test_data.campaign_id,
                        batch_size=batch_size,
                    )

        # Run with different batch sizes concurrently
        results = await asyncio.gather(
            run_process_batch(3),
            run_process_batch(7),
        )

        total_processed = sum(results)
        assert total_processed == 10

        # Verify no duplicates
        assert len(set(processed_runs)) == len(processed_runs)

    @pytest.mark.asyncio
    async def test_multiple_concurrent_workers(
        self,
        campaign_test_data,
        mock_dispatch_call,
        mock_rate_limiter,
        db_session_factory,
    ):
        """
        Test with many concurrent workers (simulating production scenario).

        5 workers each requesting 4 runs from a pool of 10.
        Should process all 10 exactly once.
        """
        mock_dispatch, processed_runs = mock_dispatch_call

        # Reset queued runs
        async with db_session_factory() as session:
            await session.execute(
                text(
                    "UPDATE queued_runs SET state = 'queued' WHERE campaign_id = :campaign_id"
                ),
                {"campaign_id": campaign_test_data.campaign_id},
            )
            await session.commit()

        async def run_process_batch():
            with patch(
                "api.services.campaign.campaign_call_dispatcher.rate_limiter"
            ) as mock_rl:
                mock_rl.acquire_token = AsyncMock(
                    side_effect=mock_rate_limiter["acquire_token"]
                )
                mock_rl.try_acquire_concurrent_slot = AsyncMock(
                    side_effect=mock_rate_limiter["try_acquire_concurrent_slot"]
                )
                mock_rl.release_concurrent_slot = AsyncMock(
                    side_effect=mock_rate_limiter["release_concurrent_slot"]
                )
                mock_rl.store_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["store_workflow_slot_mapping"]
                )
                mock_rl.get_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["get_workflow_slot_mapping"]
                )
                mock_rl.delete_workflow_slot_mapping = AsyncMock(
                    side_effect=mock_rate_limiter["delete_workflow_slot_mapping"]
                )

                dispatcher = CampaignCallDispatcher()

                with patch.object(
                    dispatcher, "dispatch_call", side_effect=mock_dispatch
                ):
                    return await dispatcher.process_batch(
                        campaign_id=campaign_test_data.campaign_id, batch_size=4
                    )

        # Run 5 workers concurrently
        results = await asyncio.gather(*[run_process_batch() for _ in range(5)])

        total_processed = sum(results)
        assert total_processed == 10

        # Verify no duplicates
        assert len(set(processed_runs)) == 10, "Duplicate runs were processed!"

    @pytest.mark.asyncio
    async def test_processing_state_transition(
        self,
        campaign_test_data,
        mock_dispatch_call,
        mock_rate_limiter,
        db_session_factory,
    ):
        """
        Test that runs transition through processing -> processed states correctly.
        """
        mock_dispatch, processed_runs = mock_dispatch_call

        # Reset queued runs
        async with db_session_factory() as session:
            await session.execute(
                text(
                    "UPDATE queued_runs SET state = 'queued' WHERE campaign_id = :campaign_id"
                ),
                {"campaign_id": campaign_test_data.campaign_id},
            )
            await session.commit()

        with patch(
            "api.services.campaign.campaign_call_dispatcher.rate_limiter"
        ) as mock_rl:
            mock_rl.acquire_token = AsyncMock(
                side_effect=mock_rate_limiter["acquire_token"]
            )
            mock_rl.try_acquire_concurrent_slot = AsyncMock(
                side_effect=mock_rate_limiter["try_acquire_concurrent_slot"]
            )
            mock_rl.release_concurrent_slot = AsyncMock(
                side_effect=mock_rate_limiter["release_concurrent_slot"]
            )
            mock_rl.store_workflow_slot_mapping = AsyncMock(
                side_effect=mock_rate_limiter["store_workflow_slot_mapping"]
            )
            mock_rl.get_workflow_slot_mapping = AsyncMock(
                side_effect=mock_rate_limiter["get_workflow_slot_mapping"]
            )
            mock_rl.delete_workflow_slot_mapping = AsyncMock(
                side_effect=mock_rate_limiter["delete_workflow_slot_mapping"]
            )

            dispatcher = CampaignCallDispatcher()

            with patch.object(dispatcher, "dispatch_call", side_effect=mock_dispatch):
                await dispatcher.process_batch(
                    campaign_id=campaign_test_data.campaign_id, batch_size=10
                )

        # Verify all runs are in 'processed' state
        async with db_session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT state, COUNT(*) as count FROM queued_runs "
                    "WHERE campaign_id = :campaign_id GROUP BY state"
                ),
                {"campaign_id": campaign_test_data.campaign_id},
            )
            states = {row[0]: row[1] for row in result.fetchall()}

        assert states.get("processed", 0) == 10
        assert states.get("queued", 0) == 0
        assert states.get("processing", 0) == 0


class TestProcessBatchEdgeCases:
    """Edge case tests for process_batch."""

    @pytest.mark.asyncio
    async def test_empty_queue(
        self, campaign_test_data, mock_rate_limiter, db_session_factory
    ):
        """Test process_batch with no queued runs returns 0."""
        # Set all runs to processed
        async with db_session_factory() as session:
            await session.execute(
                text(
                    "UPDATE queued_runs SET state = 'processed' WHERE campaign_id = :campaign_id"
                ),
                {"campaign_id": campaign_test_data.campaign_id},
            )
            await session.commit()

        with patch(
            "api.services.campaign.campaign_call_dispatcher.rate_limiter"
        ) as mock_rl:
            mock_rl.acquire_token = AsyncMock(
                side_effect=mock_rate_limiter["acquire_token"]
            )
            mock_rl.try_acquire_concurrent_slot = AsyncMock(
                side_effect=mock_rate_limiter["try_acquire_concurrent_slot"]
            )

            dispatcher = CampaignCallDispatcher()
            result = await dispatcher.process_batch(
                campaign_id=campaign_test_data.campaign_id, batch_size=5
            )

        assert result == 0

    @pytest.mark.asyncio
    async def test_campaign_not_running(
        self, campaign_test_data, mock_rate_limiter, db_session_factory
    ):
        """Test process_batch returns 0 if campaign is not in running state."""
        # Set campaign to paused
        async with db_session_factory() as session:
            await session.execute(
                text("UPDATE campaigns SET state = 'paused' WHERE id = :campaign_id"),
                {"campaign_id": campaign_test_data.campaign_id},
            )
            await session.commit()

        try:
            dispatcher = CampaignCallDispatcher()
            result = await dispatcher.process_batch(
                campaign_id=campaign_test_data.campaign_id, batch_size=5
            )
            assert result == 0
        finally:
            # Restore campaign state
            async with db_session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE campaigns SET state = 'running' WHERE id = :campaign_id"
                    ),
                    {"campaign_id": campaign_test_data.campaign_id},
                )
                await session.commit()
