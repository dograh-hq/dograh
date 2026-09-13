import json
import re
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, text, update
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.filters import apply_workflow_run_filters, get_workflow_run_order_clause
from api.db.models import CampaignModel, QueuedRunModel, WorkflowRunModel
from api.enums import TelephonyCallStatus, WorkflowRunState
from api.schemas.workflow import WorkflowRunResponseSchema
from api.services.workflow.run_usage_response import format_public_cost_info
from api.utils.recording_artifacts import get_recording_storage_key
from api.utils.telephony_address import normalize_telephony_address


def is_same_recipient_number(
    candidate: str,
    target_digits: str,
    target_canonical: str,
    target_no_plus: str,
) -> bool:
    """Decide whether a stored phone number refers to the same recipient as the target.

    This is a *consent* gate, not a data-cleanup helper: the caller acts on the
    result by activating or failing a parked campaign run on the strength of a
    permission webhook. A false positive therefore dials someone who never
    consented, or cancels someone else's lead on a stranger's refusal. So the
    rule is full-number equality on the canonical digits and nothing else. The
    only differences forgiven are punctuation and a leading "+", because
    neither carries any information about which subscriber is meant.

    In particular this deliberately does NOT infer a missing country code.
    Both sides are guaranteed to carry one:

    * Campaign leads - ``CampaignSourceSyncService.validate_source_data``
      (api/services/campaign/source_sync.py) rejects campaign creation with a
      400 if any CSV row's ``phone_number`` does not start with "+", so a
      stored lead is an international number, however it is punctuated.
    * Webhook recipients - Meta reports the recipient as a ``wa_id``, which is
      always a full international number.

    Given that, two numbers that differ by a leading 1-3 digit prefix are two
    different subscribers, not two spellings of one: "+441234567890" (UK) and
    "+1234567890" may well belong to unrelated people, and a grant or denial
    from one must never move the other's run. The target derivations are passed
    in precomputed because this runs once per candidate row.
    """
    if not candidate:
        return False

    candidate_digits = re.sub(r"\D", "", candidate)

    # 1. Canonical digit equality. Every formatting variant of one number -
    #    "+1 (555) 123-4567", "1-555-123-4567", "+15551234567", "15551234567" -
    #    reduces to the same digit string, so this covers punctuation and the
    #    optional "+" without relaxing *which* number is meant.
    if candidate_digits and candidate_digits == target_digits:
        return True

    # 2. Canonical match via normalize_telephony_address. For PSTN this agrees
    #    with (1); it matters when the target is not a PSTN number at all
    #    (e.g. a SIP address), where comparing digit runs says nothing.
    try:
        if normalize_telephony_address(candidate).canonical == target_canonical:
            return True
    except Exception:
        pass

    # 3. Leading plus-stripped string match, for the same non-PSTN case.
    return candidate.lstrip("+") == target_no_plus


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
        telephony_configuration_id: Optional[int] = None,
        whatsapp_permission_action: Optional[str] = "skip",
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
            if whatsapp_permission_action is not None:
                orchestrator_metadata["whatsapp_permission_action"] = (
                    whatsapp_permission_action
                )

            campaign = CampaignModel(
                name=name,
                workflow_id=workflow_id,
                source_type=source_type,
                source_id=source_id,
                created_by=user_id,
                organization_id=organization_id,
                retry_config=(
                    retry_config
                    if retry_config
                    else CampaignModel.retry_config.default.arg
                ),
                orchestrator_metadata=orchestrator_metadata,
                telephony_configuration_id=telephony_configuration_id,
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
                        "user_recording_url": get_recording_storage_key(
                            run.extra, "user"
                        ),
                        "bot_recording_url": get_recording_storage_key(
                            run.extra, "bot"
                        ),
                        "cost_info": format_public_cost_info(
                            run.cost_info, run.usage_info
                        ),
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

            # whatsapp_permission_action is orchestrator behaviour, not a
            # one-off of the parent run: campaign_call_dispatcher reads it with
            # a default of "skip", so leaving it out of this allowlist silently
            # downgrades a redial of a "request_and_wait" campaign to "skip" -
            # the child stops asking recipients for call permission and drops
            # every unpermitted lead instead of parking it.
            child_meta = {
                k: v
                for k, v in parent_meta.items()
                if k
                in (
                    "max_concurrency",
                    "schedule_config",
                    "circuit_breaker",
                    "whatsapp_permission_action",
                )
            }
            child_meta["parent_campaign_id"] = parent_campaign.id

            child = CampaignModel(
                name=new_name,
                workflow_id=parent_campaign.workflow_id,
                source_type=parent_campaign.source_type,
                source_id=parent_campaign.source_id,
                created_by=parent_campaign.created_by,
                organization_id=parent_campaign.organization_id,
                retry_config=(
                    retry_config
                    if retry_config
                    else CampaignModel.retry_config.default.arg
                ),
                orchestrator_metadata=child_meta,
                # Pin the same telephony configuration as the parent. Without
                # it the child falls back to the org's default outbound config
                # in get_provider_for_campaign, so a redial of a WhatsApp
                # campaign can leave via a different provider entirely - which
                # would also make the inherited whatsapp_permission_action
                # above inert. Same organization_id as the parent, so this
                # references nothing outside the tenant.
                telephony_configuration_id=parent_campaign.telephony_configuration_id,
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
        sql = text(f"""
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
            """)

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

    async def append_campaign_log(
        self,
        campaign_id: int,
        level: str,
        event: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append a timestamped entry to the campaign's logs JSON array.

        Uses a SQL-side jsonb concat so concurrent writers do not clobber
        each other's entries.
        """
        entry: Dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": level,
            "event": event,
            "message": message,
        }
        if details:
            entry["details"] = details

        async with self.async_session() as session:
            await session.execute(
                text(
                    "UPDATE campaigns "
                    "SET logs = (logs::jsonb || CAST(:entry AS jsonb))::json, "
                    "    updated_at = :now "
                    "WHERE id = :campaign_id"
                ),
                {
                    "entry": json.dumps([entry]),
                    "now": datetime.now(UTC),
                    "campaign_id": campaign_id,
                },
            )
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def increment_campaign_metadata_counter(
        self, campaign_id: int, key: str
    ) -> int:
        """Atomically increment an integer field in campaign orchestrator_metadata."""
        async with self.async_session() as session:
            result = await session.execute(
                text(
                    "UPDATE campaigns "
                    "SET orchestrator_metadata = ("
                    "        COALESCE(orchestrator_metadata::jsonb, '{}'::jsonb) "
                    "        || jsonb_build_object("
                    "            :key, "
                    "            COALESCE((orchestrator_metadata::jsonb ->> :key)::int, 0) + 1"
                    "        )"
                    "    )::json, "
                    "    updated_at = :now "
                    "WHERE id = :campaign_id "
                    "RETURNING (orchestrator_metadata::jsonb ->> :key)::int"
                ),
                {
                    "campaign_id": campaign_id,
                    "key": key,
                    "now": datetime.now(UTC),
                },
            )
            attempt = result.scalar_one()
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            return attempt

    async def mark_queued_run_processed_if_owned(self, queued_run_id: int) -> bool:
        """Move a claimed run to ``processed``, only if this claim still owns it.

        ``claim_queued_runs_for_processing`` leaves the row in ``processing``, so
        that state is the claim ticket. A run that was parked and later granted
        goes back to ``queued`` and can be claimed and completed by a different
        batch; re-marking it here would rewrite another batch's ``processed_at``
        and double count it. Returns True only when this call performed the
        transition.
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(QueuedRunModel)
                .where(
                    QueuedRunModel.id == queued_run_id,
                    QueuedRunModel.state == "processing",
                )
                .values(state="processed", processed_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            return result.rowcount > 0

    async def sync_campaign_processed_rows(self, campaign_id: int) -> int:
        """Set processed_rows to the number of finished queued runs.

        ``processed_rows`` is a cache of queued-run state, not an independent
        fact, and it used to be maintained by delta from several places (batch
        dispatch, permission denial, redial). Deltas cannot be replayed: a
        duplicate webhook or an overlapping batch inflated progress past the
        number of finished contacts, and a failed write lost it for good.
        Recomputing is idempotent, so this is the only writer of the column -
        every path that finishes a run calls it instead of adding its own one.

        The campaign row is locked before the count is taken, in two
        statements, and that ordering is the point. PostgreSQL evaluates a
        statement against the snapshot it started with, so a single
        count-and-update that blocked on this lock would afterwards write a
        count taken before the writer it waited for had committed - silently
        undoing that run. Taking the lock first and counting in a new statement
        gives the count a snapshot that includes everything committed up to the
        moment the lock was granted.
        """
        async with self.async_session() as session:
            locked = await session.execute(
                text("SELECT id FROM campaigns WHERE id = :campaign_id FOR UPDATE"),
                {"campaign_id": campaign_id},
            )
            if locked.scalar_one_or_none() is None:
                await session.rollback()
                raise ValueError(f"Campaign {campaign_id} not found")

            result = await session.execute(
                text(
                    "UPDATE campaigns SET "
                    "processed_rows = ("
                    "  SELECT count(*) FROM queued_runs "
                    "  WHERE queued_runs.campaign_id = campaigns.id "
                    "  AND queued_runs.state IN ('processed', 'failed')"
                    "), "
                    "updated_at = :now "
                    "WHERE campaigns.id = :campaign_id "
                    "RETURNING campaigns.processed_rows"
                ),
                {"campaign_id": campaign_id, "now": datetime.now(UTC)},
            )
            actual = result.scalar_one()
            await session.commit()
            return actual

    async def merge_campaign_orchestrator_metadata(
        self, campaign_id: int, updates: Dict[str, Any]
    ) -> Optional[CampaignModel]:
        """Atomically merge `updates` into campaigns.orchestrator_metadata (SQL-side jsonb ||).

        A last-write-wins Python read/merge/write would drop concurrent writers'
        keys; the jsonb `||` merge below is done entirely in SQL so concurrent
        callers each only ever add or overwrite their own keys.
        """
        async with self.async_session() as session:
            result = await session.execute(
                text(
                    "UPDATE campaigns "
                    "SET orchestrator_metadata = ("
                    "        COALESCE(orchestrator_metadata::jsonb, '{}'::jsonb) "
                    "        || CAST(:updates AS jsonb)"
                    "    )::json, "
                    "    updated_at = :now "
                    "WHERE id = :campaign_id "
                    "RETURNING id"
                ),
                {
                    "campaign_id": campaign_id,
                    "updates": json.dumps(updates),
                    "now": datetime.now(UTC),
                },
            )
            row = result.scalar_one_or_none()
            if row is None:
                await session.rollback()
                return None
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            return await session.get(CampaignModel, campaign_id)

    async def reset_campaign_metadata_counter(self, campaign_id: int, key: str) -> None:
        """Remove a counter field from campaign orchestrator_metadata."""
        async with self.async_session() as session:
            await session.execute(
                text(
                    "UPDATE campaigns "
                    "SET orchestrator_metadata = ("
                    "        COALESCE(orchestrator_metadata::jsonb, '{}'::jsonb) - :key"
                    "    )::json, "
                    "    updated_at = :now "
                    "WHERE id = :campaign_id"
                ),
                {
                    "campaign_id": campaign_id,
                    "key": key,
                    "now": datetime.now(UTC),
                },
            )
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise

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

    async def return_processing_queued_runs_without_workflow(
        self, queued_run_ids: list[int]
    ) -> int:
        """Return claimed queued_runs to queued if no workflow was created for them."""
        if not queued_run_ids:
            return 0

        workflow_exists = (
            select(WorkflowRunModel.id)
            .where(WorkflowRunModel.queued_run_id == QueuedRunModel.id)
            .exists()
        )
        async with self.async_session() as session:
            result = await session.execute(
                update(QueuedRunModel)
                .where(
                    QueuedRunModel.id.in_(queued_run_ids),
                    QueuedRunModel.state == "processing",
                    ~workflow_exists,
                )
                .values(state="queued")
            )
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            return result.rowcount or 0

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
                WorkflowRunModel.usage_info["call_duration_seconds"]
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
                    WorkflowRunModel.usage_info,
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

    async def get_claimable_queued_runs_count(
        self,
        campaign_id: int,
        before: Optional[datetime] = None,
    ) -> int:
        """Get count of queued runs that are claimable right now (unscheduled or scheduled <= before)."""
        now = before or datetime.now(UTC)
        async with self.async_session() as session:
            query = select(func.count(QueuedRunModel.id)).where(
                QueuedRunModel.campaign_id == campaign_id,
                QueuedRunModel.state == "queued",
                or_(
                    QueuedRunModel.scheduled_for.is_(None),
                    QueuedRunModel.scheduled_for <= now,
                ),
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

    async def get_queued_runs_awaiting_whatsapp_permission(
        self, phone_number: str
    ) -> List[QueuedRunModel]:
        """Find active queued runs waiting for WhatsApp call permission from this phone number."""
        if not phone_number:
            return []

        raw_trimmed = phone_number.strip()
        target_digits = re.sub(r"\D", "", raw_trimmed)
        if not target_digits:
            return []

        try:
            target_canonical = normalize_telephony_address(raw_trimmed).canonical
        except Exception:
            target_canonical = f"+{target_digits}"

        target_no_plus = target_canonical.lstrip("+")

        # Build candidate filter conditions for SQL-level filtering to avoid
        # loading all parked runs into memory.
        candidate_filters = [
            QueuedRunModel.context_variables["phone_number"].as_string() == raw_trimmed,
            QueuedRunModel.context_variables["phone_number"].as_string() == target_digits,
            QueuedRunModel.context_variables["phone_number"].as_string() == f"+{target_digits}",
            QueuedRunModel.context_variables["phone_number"].as_string() == target_canonical,
            QueuedRunModel.context_variables["phone_number"].as_string() == target_no_plus,
        ]

        # Formatted variants ("+1 (555) 123-4567", "+33 6 12 34 56 78") never
        # equal any of the strings above, so compare the stored number with its
        # punctuation removed - exactly the reduction is_same_recipient_number
        # performs in Python. This replaces the previous last-7 / last-4 digit
        # ILIKE prefilter, which had a real recall hole: punctuation inside the
        # trailing digits defeats both windows at once. "+33 6 12 34 56 78"
        # contains neither "2345678" nor "5678" as a substring, so a parked
        # French lead was dropped in SQL before the matcher ever saw it - and
        # 2-digit grouping is the normal spelling across much of Europe, not an
        # exotic case. Stripping non-digits in SQL is immune to any separator.
        if target_digits:
            candidate_filters.append(
                func.regexp_replace(
                    QueuedRunModel.context_variables["phone_number"].as_string(),
                    "[^0-9]",
                    "",
                    "g",
                )
                == target_digits
            )

        async with self.async_session() as session:
            query = select(QueuedRunModel).where(
                QueuedRunModel.state == "queued",
                QueuedRunModel.retry_reason == "awaiting_whatsapp_permission",
                or_(*candidate_filters),
            )
            result = await session.execute(query)
            runs = list(result.scalars().all())

            # Robust matching in Python across formatting differences. Only
            # context_variables["phone_number"] is consulted, so a row the broad
            # ILIKE prefilter pulled in on some other field is discarded here.
            return [
                r
                for r in runs
                if is_same_recipient_number(
                    str((r.context_variables or {}).get("phone_number") or "").strip(),
                    target_digits=target_digits,
                    target_canonical=target_canonical,
                    target_no_plus=target_no_plus,
                )
            ]

    async def get_all_queued_runs_awaiting_whatsapp_permission(
        self, campaign_id: Optional[int] = None
    ) -> List[QueuedRunModel]:
        """Find all active queued runs waiting for WhatsApp call permission across campaigns or for a specific campaign."""
        async with self.async_session() as session:
            conditions = [
                QueuedRunModel.state == "queued",
                QueuedRunModel.retry_reason == "awaiting_whatsapp_permission",
            ]
            if campaign_id is not None:
                conditions.append(QueuedRunModel.campaign_id == campaign_id)
            query = select(QueuedRunModel).where(*conditions)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def activate_queued_run_for_immediate_dial(
        self, queued_run_id: int
    ) -> Optional[QueuedRunModel]:
        """Clears scheduled_for so the run is picked up in the next campaign batch,
        and removes awaiting_permission disposition on the linked workflow run.

        Returns None - changing nothing - if the run is no longer parked on this
        permission. Meta redelivers permission webhooks, and the same grant can
        additionally arrive over the messages webhook and the cron sync, so the
        caller's "still queued and awaiting permission" read is stale by the
        time we get here. The conditional UPDATE below is the atomic gate: only
        the writer that observes the row still in ('queued',
        'awaiting_whatsapp_permission') proceeds, so a duplicate grant cannot
        un-fail a denied run or re-arm one that has already been claimed.
        """
        async with self.async_session() as session:
            gate = await session.execute(
                update(QueuedRunModel)
                .where(
                    QueuedRunModel.id == queued_run_id,
                    QueuedRunModel.state == "queued",
                    QueuedRunModel.retry_reason == "awaiting_whatsapp_permission",
                )
                .values(scheduled_for=None, retry_reason="permission_granted")
                .execution_options(synchronize_session=False)
            )
            if gate.rowcount == 0:
                await session.rollback()
                return None

            # The gate already persisted both fields, and it holds the row lock
            # until this transaction commits, so this read sees them. Assigning
            # them again through the ORM would make a second writer for state
            # the gate alone should own.
            run = await session.get(QueuedRunModel, queued_run_id)
            if not run:
                await session.rollback()
                return None

            # Reset awaiting_permission disposition on existing workflow run so UI
            # immediately reverts to the default condition for a live/in-flight run
            wf_query = (
                select(WorkflowRunModel)
                .where(WorkflowRunModel.queued_run_id == queued_run_id)
                .order_by(WorkflowRunModel.created_at.desc())
            )
            wf_result = await session.execute(wf_query)
            wf_run = wf_result.scalars().first()
            if wf_run and not wf_run.is_completed:
                ctx = dict(wf_run.gathered_context or {})
                ctx.pop("call_disposition", None)
                ctx.pop("mapped_call_disposition", None)
                ctx.pop("call_status", None)
                ctx.pop("error", None)
                wf_run.gathered_context = ctx
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(wf_run, "gathered_context")

            await session.commit()
            await session.refresh(run)
            return run

    async def fail_queued_run_permission_denied(
        self, queued_run_id: int
    ) -> Optional[QueuedRunModel]:
        """Marks a queued run as failed when recipient denies WhatsApp call permission.

        Returns None - changing nothing - if the run is no longer parked on this
        permission. The previous "state not in (failed, processed)" check was
        read from an unlocked row, so two concurrent deliveries of the same
        denial (Meta retries webhooks, and the messages webhook and cron sync
        can carry the same event) could both observe 'queued' and each add one
        to failed_rows. It also could not tell a fresh denial from a stale one
        for a run that had since been granted and claimed. The conditional
        UPDATE below is the atomic gate: exactly one writer sees the row in
        ('queued', 'awaiting_whatsapp_permission'), and only that writer moves
        failed_rows or recomputes processed_rows.
        """
        async with self.async_session() as session:
            now = datetime.now(UTC)
            gate = await session.execute(
                update(QueuedRunModel)
                .where(
                    QueuedRunModel.id == queued_run_id,
                    QueuedRunModel.state == "queued",
                    QueuedRunModel.retry_reason == "awaiting_whatsapp_permission",
                )
                .values(
                    state="failed",
                    retry_reason="permission_denied",
                    processed_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if gate.rowcount == 0:
                await session.rollback()
                return None

            run = await session.get(QueuedRunModel, queued_run_id)
            if not run:
                await session.rollback()
                return None

            # The gate proved this run was still parked, so failed_rows moves
            # exactly once per run. Incrementing SQL-side rather than reading
            # the campaign and writing back keeps this from clobbering a
            # concurrent writer of the same column.
            #
            # processed_rows is deliberately NOT touched here. It is derived
            # from queued-run state, and this transaction is what makes that
            # state true; adding a delta as well would make this a second
            # writer of a value sync_campaign_processed_rows recomputes, and
            # the two cannot both be right - that increment is what the
            # recompute then had to be clamped not to undo, which in turn made
            # an overcount permanent.
            #
            # The recompute is the caller's, once per campaign after its runs
            # are committed: it locks the campaign row, so doing it per run
            # would serialise N exclusive-lock recounts on a webhook that
            # denied one recipient with N parked leads.
            if run.campaign_id:
                await session.execute(
                    update(CampaignModel)
                    .where(CampaignModel.id == run.campaign_id)
                    .values(
                        failed_rows=func.coalesce(CampaignModel.failed_rows, 0) + 1,
                    )
                    .execution_options(synchronize_session=False)
                )

            run.state = "failed"
            run.retry_reason = "permission_denied"
            run.processed_at = now
            wf_query = (
                select(WorkflowRunModel)
                .where(WorkflowRunModel.queued_run_id == queued_run_id)
                .order_by(WorkflowRunModel.created_at.desc())
            )
            wf_result = await session.execute(wf_query)
            wf_run = wf_result.scalars().first()
            if wf_run and not wf_run.is_completed:
                wf_run.is_completed = True
                wf_run.state = WorkflowRunState.COMPLETED.value
                wf_run.gathered_context = {
                    **(wf_run.gathered_context or {}),
                    "call_disposition": TelephonyCallStatus.PERMISSION_DENIED.value,
                    "mapped_call_disposition": TelephonyCallStatus.PERMISSION_DENIED.value,
                    "call_status": TelephonyCallStatus.PERMISSION_DENIED.value,
                    "error": "WhatsApp call permission was denied by recipient",
                }
            await session.commit()
            await session.refresh(run)
        return run
