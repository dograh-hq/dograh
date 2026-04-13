from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, text
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
        circuit_breaker: Optional[dict] = None,
    ) -> CampaignModel:
        """Create a new campaign"""
        async with self.async_session() as session:
            # Build orchestrator_metadata with max_concurrency if provided
            orchestrator_metadata = {}
            if max_concurrency is not None:
                orchestrator_metadata["max_concurrency"] = max_concurrency
            if schedule_config is not None:
                orchestrator_metadata["schedule_config"] = schedule_config
            if circuit_breaker is not None:
                orchestrator_metadata["circuit_breaker"] = circuit_breaker

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

    async def get_latest_campaign(
        self,
        organization_id: int,
    ) -> Optional[CampaignModel]:
        """Get the most recently created campaign for an organization"""
        async with self.async_session() as session:
            query = (
                select(CampaignModel)
                .where(CampaignModel.organization_id == organization_id)
                .order_by(CampaignModel.created_at.desc())
                .limit(1)
            )
            result = await session.execute(query)
            return result.scalars().first()

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

    async def create_redial_campaign(
        self,
        parent_campaign: CampaignModel,
        new_name: str,
        retry_config: Optional[dict],
        queued_runs_data: list[dict],
    ) -> CampaignModel:
        """Atomically create a redial child campaign, seed its queued_runs, and
        link the parent.

        - The child inherits `workflow_id`, `source_type`, `source_id`,
          `created_by`, `organization_id`, and orchestrator settings
          (`max_concurrency`, `schedule_config`, `circuit_breaker`) from the
          parent. `parent_campaign_id` is stored in the child's
          orchestrator_metadata.
        - `queued_runs_data` should be pre-built dicts with campaign_id set to 0
          (will be replaced once the child id is known).
        - Parent's orchestrator_metadata gets `redialed_campaign_id` set.
        - All inserts/updates happen in a single transaction.
        """
        async with self.async_session() as session:
            parent_meta = dict(parent_campaign.orchestrator_metadata or {})
            if parent_meta.get("redialed_campaign_id"):
                raise ValueError(
                    f"Campaign {parent_campaign.id} has already been redialed"
                )

            child_meta = {
                k: v
                for k, v in parent_meta.items()
                if k in ("max_concurrency", "schedule_config", "circuit_breaker")
            }
            child_meta["parent_campaign_id"] = parent_campaign.id

            child = CampaignModel(
                name=new_name,
                workflow_id=parent_campaign.workflow_id,
                source_type=parent_campaign.source_type,
                source_id=parent_campaign.source_id,
                created_by=parent_campaign.created_by,
                organization_id=parent_campaign.organization_id,
                retry_config=retry_config
                if retry_config
                else CampaignModel.retry_config.default.arg,
                orchestrator_metadata=child_meta,
                rate_limit_per_second=parent_campaign.rate_limit_per_second,
                total_rows=len(queued_runs_data),
                source_sync_status="completed",
            )
            session.add(child)
            await session.flush()  # assign child.id

            for data in queued_runs_data:
                data["campaign_id"] = child.id
            session.add_all([QueuedRunModel(**data) for data in queued_runs_data])

            parent_meta["redialed_campaign_id"] = child.id
            parent_stmt = select(CampaignModel).where(
                CampaignModel.id == parent_campaign.id
            )
            parent_result = await session.execute(parent_stmt)
            parent_row = parent_result.scalar_one()
            parent_row.orchestrator_metadata = parent_meta
            parent_row.updated_at = datetime.now(UTC)

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(child)
            return child

    async def get_redial_candidates(
        self,
        campaign_id: int,
        include_voicemail: bool,
        include_no_answer: bool,
        include_busy: bool,
    ) -> list[dict]:
        """Return root context_variables for subscribers whose LATEST
        workflow_run indicates the call should be redialed.

        A subscriber (identified by `source_uuid`) is a redial candidate iff
        the latest workflow_run (by created_at) for that source_uuid has a
        `call_tags` entry matching any of the selected failure reasons. Uses
        the root queued_run (retry_count=0) for the original context.
        """
        tag_clauses = []
        if include_voicemail:
            tag_clauses.append(
                "(lr.gathered_context::jsonb -> 'call_tags') @> '[\"voicemail_detected\"]'::jsonb"
            )
        if include_no_answer:
            tag_clauses.append(
                "(lr.gathered_context::jsonb -> 'call_tags') @> '[\"telephony_no-answer\"]'::jsonb"
            )
        if include_busy:
            tag_clauses.append(
                "(lr.gathered_context::jsonb -> 'call_tags') @> '[\"telephony_busy\"]'::jsonb"
            )

        if not tag_clauses:
            return []

        tag_filter = " OR ".join(tag_clauses)
        # Retries create new queued_runs with suffixed source_uuids linked via
        # parent_queued_run_id, so group by the ROOT queued_run using a
        # recursive walk and pick the latest workflow_run across the tree.
        sql = text(
            f"""
            WITH RECURSIVE run_tree AS (
                SELECT id AS root_id, id AS run_id
                FROM queued_runs
                WHERE campaign_id = :cid
                  AND parent_queued_run_id IS NULL
                UNION ALL
                SELECT rt.root_id, q.id
                FROM run_tree rt
                JOIN queued_runs q ON q.parent_queued_run_id = rt.run_id
                WHERE q.campaign_id = :cid
            ),
            latest_run_per_root AS (
                SELECT DISTINCT ON (rt.root_id)
                    rt.root_id,
                    wr.gathered_context
                FROM run_tree rt
                JOIN workflow_runs wr
                  ON wr.queued_run_id = rt.run_id
                 AND wr.campaign_id = :cid
                ORDER BY rt.root_id, wr.created_at DESC
            )
            SELECT q0.source_uuid, q0.context_variables
            FROM queued_runs q0
            JOIN latest_run_per_root lr ON lr.root_id = q0.id
            WHERE q0.campaign_id = :cid
              AND ({tag_filter})
            """
        )

        async with self.async_session() as session:
            result = await session.execute(sql, {"cid": campaign_id})
            return [
                {"source_uuid": row[0], "context_variables": row[1]}
                for row in result.all()
            ]

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

    async def get_queued_runs_stats_for_campaigns(
        self, campaign_ids: List[int]
    ) -> Dict[int, Dict[str, int]]:
        """Return {campaign_id: {"total": N, "executed": M}} for given campaigns.

        "executed" means queued runs in the "processed" state.
        """
        if not campaign_ids:
            return {}
        async with self.async_session() as session:
            query = (
                select(
                    QueuedRunModel.campaign_id,
                    QueuedRunModel.state,
                    func.count(QueuedRunModel.id),
                )
                .where(QueuedRunModel.campaign_id.in_(campaign_ids))
                .group_by(QueuedRunModel.campaign_id, QueuedRunModel.state)
            )
            result = await session.execute(query)
            stats: Dict[int, Dict[str, int]] = {
                cid: {"total": 0, "executed": 0} for cid in campaign_ids
            }
            for campaign_id, state, count in result.all():
                stats[campaign_id]["total"] += count
                if state == "processed":
                    stats[campaign_id]["executed"] += count
            return stats

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

    async def get_completed_runs_for_report(
        self,
        *,
        campaign_id: Optional[int] = None,
        workflow_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> list:
        """Get completed workflow runs for a run report CSV.

        Scope the query by exactly one of campaign_id or workflow_id.
        Returns rows with only the columns needed for report generation.
        """
        if (campaign_id is None) == (workflow_id is None):
            raise ValueError("Provide exactly one of campaign_id or workflow_id")

        async with self.async_session() as session:
            conditions = [
                WorkflowRunModel.is_completed.is_(True),
                WorkflowRunModel.cost_info["call_duration_seconds"]
                .as_string()
                .isnot(None),
            ]
            if campaign_id is not None:
                conditions.append(WorkflowRunModel.campaign_id == campaign_id)
            if workflow_id is not None:
                conditions.append(WorkflowRunModel.workflow_id == workflow_id)
            if start_date is not None:
                conditions.append(WorkflowRunModel.created_at >= start_date)
            if end_date is not None:
                conditions.append(WorkflowRunModel.created_at <= end_date)

            query = (
                select(
                    WorkflowRunModel.id,
                    WorkflowRunModel.workflow_id,
                    WorkflowRunModel.definition_id,
                    WorkflowRunModel.campaign_id,
                    WorkflowRunModel.created_at,
                    WorkflowRunModel.initial_context,
                    WorkflowRunModel.gathered_context,
                    WorkflowRunModel.cost_info,
                    WorkflowRunModel.public_access_token,
                )
                .where(*conditions)
                .order_by(WorkflowRunModel.created_at.desc())
            )
            result = await session.execute(query)
            return list(result.all())

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
                    .order_by(func.random())
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
