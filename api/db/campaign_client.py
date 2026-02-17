from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.filters import apply_workflow_run_filters, get_workflow_run_order_clause
from api.db.models import CampaignModel, QueuedRunModel, WorkflowRunModel
from api.schemas.workflow import WorkflowRunResponseSchema


class CampaignClient(BaseDBClient):
    async def create_campaign(
        self,
        name: str,
        workflow_id: int,
        source_type: str,
        source_id: str,
        user_id: int,
        organization_id: int,
        retry_config: Optional[dict] = None,
        max_concurrency: Optional[int] = None,
        schedule_config: Optional[dict] = None,
    ) -> CampaignModel:
        """Create a new campaign"""
        async with self.async_session() as session:
            # Build orchestrator_metadata with max_concurrency if provided
            orchestrator_metadata = {}
            if max_concurrency is not None:
                orchestrator_metadata["max_concurrency"] = max_concurrency
            if schedule_config is not None:
                orchestrator_metadata["schedule_config"] = schedule_config

            campaign = CampaignModel(
                name=name,
                workflow_id=workflow_id,
                source_type=source_type,
                source_id=source_id,
                created_by=user_id,
                organization_id=organization_id,
                retry_config=retry_config
                if retry_config
                else CampaignModel.retry_config.default.arg,
                orchestrator_metadata=orchestrator_metadata,
            )
            session.add(campaign)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(campaign)
            return campaign

    async def get_campaigns(
        self,
        organization_id: int,
    ) -> list[CampaignModel]:
        """Get all campaigns for organization"""
        async with self.async_session() as session:
            query = (
                select(CampaignModel)
                .where(CampaignModel.organization_id == organization_id)
                .order_by(CampaignModel.created_at.desc())
            )

            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_campaign(
        self,
        campaign_id: int,
        organization_id: int,
    ) -> Optional[CampaignModel]:
        """Get single campaign by ID, ensuring organization access"""
        async with self.async_session() as session:
            query = select(CampaignModel).where(
                CampaignModel.id == campaign_id,
                CampaignModel.organization_id == organization_id,
            )
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def update_campaign_state(
        self,
        campaign_id: int,
        state: str,
        organization_id: int,
    ) -> CampaignModel:
        """Update campaign state (start/pause/resume)"""
        async with self.async_session() as session:
            query = select(CampaignModel).where(
                CampaignModel.id == campaign_id,
                CampaignModel.organization_id == organization_id,
            )
            result = await session.execute(query)
            campaign = result.scalar_one_or_none()

            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")

            campaign.state = state
            if state == "running" and not campaign.started_at:
                campaign.started_at = datetime.now(UTC)
            elif state in ["completed", "failed"]:
                campaign.completed_at = datetime.now(UTC)

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(campaign)
            return campaign

    async def update_campaign_progress(
        self,
        campaign_id: int,
        processed_rows: int,
        failed_rows: int,
        organization_id: int,
    ) -> None:
        """Update campaign progress counters"""
        async with self.async_session() as session:
            query = select(CampaignModel).where(
                CampaignModel.id == campaign_id,
                CampaignModel.organization_id == organization_id,
            )
            result = await session.execute(query)
            campaign = result.scalar_one_or_none()

            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")

            campaign.processed_rows = processed_rows
            campaign.failed_rows = failed_rows
            campaign.updated_at = datetime.now(UTC)

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e

    async def get_campaign_runs(
        self,
        campaign_id: int,
        organization_id: int,
    ) -> list[WorkflowRunModel]:
        """Get workflow runs for a campaign"""
        async with self.async_session() as session:
            # First verify campaign belongs to organization
            campaign_query = select(CampaignModel).where(
                CampaignModel.id == campaign_id,
                CampaignModel.organization_id == organization_id,
            )
            campaign_result = await session.execute(campaign_query)
            campaign = campaign_result.scalar_one_or_none()

            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")

            query = (
                select(WorkflowRunModel)
                .where(WorkflowRunModel.campaign_id == campaign_id)
                .order_by(WorkflowRunModel.created_at.desc())
            )

            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_campaign_runs_paginated(
        self,
        campaign_id: int,
        organization_id: int,
        limit: int = 50,
        offset: int = 0,
        filters: Optional[List[Dict[str, Any]]] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = "desc",
    ) -> tuple[list[WorkflowRunResponseSchema], int]:
        """Get workflow runs for a campaign with pagination, filters and sorting"""
        async with self.async_session() as session:
            # First verify campaign belongs to organization
            campaign_query = select(CampaignModel).where(
                CampaignModel.id == campaign_id,
                CampaignModel.organization_id == organization_id,
            )
            campaign_result = await session.execute(campaign_query)
            campaign = campaign_result.scalar_one_or_none()

            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")

            # Build base query
            base_query = select(WorkflowRunModel).where(
                WorkflowRunModel.campaign_id == campaign_id
            )

            # Apply filters
            base_query = apply_workflow_run_filters(base_query, filters)

            # Count total with filters
            count_query = base_query.with_only_columns(func.count(WorkflowRunModel.id))
            count_result = await session.execute(count_query)
            total_count = count_result.scalar()

            # Get paginated results with filters and sorting
            order_clause = get_workflow_run_order_clause(sort_by, sort_order)
            result = await session.execute(
                base_query.order_by(order_clause).limit(limit).offset(offset)
            )

            runs = [
                WorkflowRunResponseSchema.model_validate(
                    {
                        "id": run.id,
                        "workflow_id": run.workflow_id,
                        "name": run.name,
                        "mode": run.mode,
                        "created_at": run.created_at,
                        "is_completed": run.is_completed,
                        "recording_url": run.recording_url,
                        "transcript_url": run.transcript_url,
                        "cost_info": {
                            "dograh_token_usage": (
                                run.cost_info.get("dograh_token_usage")
                                if run.cost_info
                                and "dograh_token_usage" in run.cost_info
                                else round(
                                    float(run.cost_info.get("total_cost_usd", 0)) * 100,
                                    2,
                                )
                                if run.cost_info and "total_cost_usd" in run.cost_info
                                else 0
                            ),
                            "call_duration_seconds": int(
                                round(run.cost_info.get("call_duration_seconds") or 0)
                            )
                            if run.cost_info
                            else None,
                        }
                        if run.cost_info
                        else None,
                        "definition_id": run.definition_id,
                        "initial_context": run.initial_context,
                        "gathered_context": run.gathered_context,
                        "call_type": run.call_type,
                    }
                )
                for run in result.scalars().all()
            ]
            return runs, total_count

    async def get_campaign_by_id(self, campaign_id: int) -> Optional[CampaignModel]:
        """Get campaign by ID without organization check (for internal use)"""
        async with self.async_session() as session:
            query = select(CampaignModel).where(CampaignModel.id == campaign_id)
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def update_campaign(self, campaign_id: int, **kwargs) -> CampaignModel:
        """Update campaign with arbitrary fields"""
        async with self.async_session() as session:
            query = select(CampaignModel).where(CampaignModel.id == campaign_id)
            result = await session.execute(query)
            campaign = result.scalar_one_or_none()

            if not campaign:
                raise ValueError(f"Campaign {campaign_id} not found")

            # Update fields
            for key, value in kwargs.items():
                if hasattr(campaign, key):
                    setattr(campaign, key, value)

            campaign.updated_at = datetime.now(UTC)

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(campaign)
            return campaign

    # QueuedRun methods
    async def bulk_create_queued_runs(self, queued_runs_data: list[dict]) -> None:
        """Bulk create queued runs"""
        async with self.async_session() as session:
            queued_runs = [QueuedRunModel(**data) for data in queued_runs_data]
            session.add_all(queued_runs)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e

    async def update_queued_run(self, queued_run_id: int, **kwargs) -> QueuedRunModel:
        """Update queued run"""
        async with self.async_session() as session:
            query = select(QueuedRunModel).where(QueuedRunModel.id == queued_run_id)
            result = await session.execute(query)
            queued_run = result.scalar_one_or_none()

            if not queued_run:
                raise ValueError(f"QueuedRun {queued_run_id} not found")

            # Update fields
            for key, value in kwargs.items():
                if hasattr(queued_run, key):
                    setattr(queued_run, key, value)

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(queued_run)
            return queued_run

    async def count_queued_runs(
        self, campaign_id: int, state: Optional[str] = None
    ) -> int:
        """Count queued runs, optionally filtered by state"""
        async with self.async_session() as session:
            query = select(func.count(QueuedRunModel.id)).where(
                QueuedRunModel.campaign_id == campaign_id
            )
            if state:
                query = query.where(QueuedRunModel.state == state)

            result = await session.execute(query)
            return result.scalar() or 0

    async def get_workflow_runs_by_campaign(
        self, campaign_id: int
    ) -> list[WorkflowRunModel]:
        """Get all workflow runs for a campaign (internal use)"""
        async with self.async_session() as session:
            query = (
                select(WorkflowRunModel)
                .where(WorkflowRunModel.campaign_id == campaign_id)
                .order_by(WorkflowRunModel.created_at)
            )
            result = await session.execute(query)
            return list(result.scalars().all())

    async def create_queued_run(
        self,
        campaign_id: int,
        source_uuid: str,
        context_variables: dict,
        state: str = "queued",
        retry_count: int = 0,
        parent_queued_run_id: Optional[int] = None,
        scheduled_for: Optional[datetime] = None,
        retry_reason: Optional[str] = None,
    ) -> QueuedRunModel:
        """Create a single queued run with retry support"""
        async with self.async_session() as session:
            queued_run = QueuedRunModel(
                campaign_id=campaign_id,
                source_uuid=source_uuid,
                context_variables=context_variables,
                state=state,
                retry_count=retry_count,
                parent_queued_run_id=parent_queued_run_id,
                scheduled_for=scheduled_for,
                retry_reason=retry_reason,
            )
            session.add(queued_run)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(queued_run)
            return queued_run

    async def get_queued_run_by_id(
        self, queued_run_id: int
    ) -> Optional[QueuedRunModel]:
        """Get a queued run by ID"""
        async with self.async_session() as session:
            query = select(QueuedRunModel).where(QueuedRunModel.id == queued_run_id)
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_campaigns_by_status(self, statuses: list[str]) -> list[CampaignModel]:
        """Get campaigns by status"""
        async with self.async_session() as session:
            query = (
                select(CampaignModel)
                .where(CampaignModel.state.in_(statuses))
                .order_by(CampaignModel.created_at.desc())
            )
            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_queued_runs_count(self, campaign_id: int, states: list[str]) -> int:
        """Get count of queued runs for a campaign in specified states"""
        async with self.async_session() as session:
            query = select(func.count(QueuedRunModel.id)).where(
                QueuedRunModel.campaign_id == campaign_id,
                QueuedRunModel.state.in_(states),
            )
            result = await session.execute(query)
            return result.scalar() or 0

    async def get_scheduled_runs_count(
        self,
        campaign_id: int,
        scheduled_before: Optional[datetime] = None,
        scheduled_after: Optional[datetime] = None,
    ) -> int:
        """Get count of scheduled runs for a campaign"""
        async with self.async_session() as session:
            conditions = [
                QueuedRunModel.campaign_id == campaign_id,
                QueuedRunModel.scheduled_for.isnot(None),
                QueuedRunModel.state == "queued",
            ]

            if scheduled_before:
                conditions.append(QueuedRunModel.scheduled_for <= scheduled_before)
            if scheduled_after:
                conditions.append(QueuedRunModel.scheduled_for > scheduled_after)

            query = select(func.count(QueuedRunModel.id)).where(*conditions)
            result = await session.execute(query)
            return result.scalar() or 0

    async def claim_queued_runs_for_processing(
        self,
        campaign_id: int,
        scheduled_before: datetime,
        limit: int = 10,
    ) -> list[QueuedRunModel]:
        """
        Atomically claim queued runs for processing using SELECT FOR UPDATE SKIP LOCKED.

        This method is thread-safe - multiple workers can call it concurrently without
        processing the same runs. It:
        1. Prioritizes scheduled retries that are due
        2. Falls back to regular queued runs if more slots available
        3. Locks selected rows and marks them as 'processing' atomically

        Returns: List of claimed QueuedRunModel objects
        """
        async with self.async_session() as session:
            claimed_runs = []

            # First, get scheduled retries that are due (with lock)
            scheduled_query = (
                select(QueuedRunModel)
                .where(
                    QueuedRunModel.campaign_id == campaign_id,
                    QueuedRunModel.state == "queued",
                    QueuedRunModel.scheduled_for.isnot(None),
                    QueuedRunModel.scheduled_for <= scheduled_before,
                )
                .order_by(QueuedRunModel.scheduled_for)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )

            scheduled_result = await session.execute(scheduled_query)
            scheduled_runs = list(scheduled_result.scalars().all())

            # Mark scheduled runs as processing
            for run in scheduled_runs:
                run.state = "processing"
                claimed_runs.append(run)

            remaining_slots = limit - len(scheduled_runs)

            # Then get regular queued runs if we have remaining slots
            if remaining_slots > 0:
                regular_query = (
                    select(QueuedRunModel)
                    .where(
                        QueuedRunModel.campaign_id == campaign_id,
                        QueuedRunModel.state == "queued",
                        QueuedRunModel.scheduled_for.is_(None),
                    )
                    .order_by(QueuedRunModel.created_at)
                    .limit(remaining_slots)
                    .with_for_update(skip_locked=True)
                )

                regular_result = await session.execute(regular_query)
                regular_runs = list(regular_result.scalars().all())

                # Mark regular runs as processing
                for run in regular_runs:
                    run.state = "processing"
                    claimed_runs.append(run)

            # Commit the state changes
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e

            # Refresh to get updated state
            for run in claimed_runs:
                await session.refresh(run)

            return claimed_runs
