"""Database client for durable outbound webhook deliveries.

Persists one row per webhook node per workflow run and exposes the state
transitions the delivery task and sweeper need: create (pending), succeed,
schedule the next retry, and park as dead-letter. Mirrors the campaign retry
pattern -- the row is the source of truth, ``scheduled_for`` gates due work.
"""

from datetime import UTC, datetime, timedelta
from typing import List, Optional, Tuple

from loguru import logger
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from api.db.base_client import BaseDBClient
from api.db.models import WebhookDeliveryModel, WorkflowModel, WorkflowRunModel


class WebhookDeliveryClient(BaseDBClient):
    """Client for managing persisted webhook delivery records."""

    async def create_webhook_delivery(
        self,
        workflow_run_id: int,
        organization_id: int,
        endpoint_url: str,
        payload: dict,
        max_attempts: int,
        http_method: str = "POST",
        webhook_name: Optional[str] = None,
        custom_headers: Optional[list] = None,
        credential_uuid: Optional[str] = None,
        webhook_node_id: Optional[str] = None,
        scheduled_for: Optional[datetime] = None,
    ) -> Tuple[WebhookDeliveryModel, bool]:
        """Get-or-create the ``pending`` delivery for this run + webhook node.

        Idempotent on ``(workflow_run_id, webhook_node_id)``: a retried
        ``run_integrations`` returns the existing row instead of creating (and
        sending) a duplicate. Returns ``(delivery, created)`` so the caller only
        enqueues a send for a freshly-created row.
        """
        async with self.async_session() as session:
            run_scope_result = await session.execute(
                select(WorkflowRunModel.id, WorkflowModel.organization_id)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(WorkflowRunModel.id == workflow_run_id)
            )
            run_scope = run_scope_result.one_or_none()
            if run_scope is None:
                raise ValueError(f"Workflow run {workflow_run_id} not found")

            _, run_organization_id = run_scope
            if run_organization_id is None:
                raise ValueError(
                    f"Workflow run {workflow_run_id} is not associated with an organization"
                )
            if run_organization_id != organization_id:
                raise ValueError(
                    f"Workflow run {workflow_run_id} belongs to organization "
                    f"{run_organization_id}, not {organization_id}"
                )

            delivery = WebhookDeliveryModel(
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                webhook_name=webhook_name,
                webhook_node_id=webhook_node_id,
                endpoint_url=endpoint_url,
                http_method=http_method,
                payload=payload,
                custom_headers=custom_headers,
                credential_uuid=credential_uuid,
                max_attempts=max_attempts,
                status="pending",
                attempt_count=0,
                scheduled_for=scheduled_for or datetime.now(UTC),
            )
            session.add(delivery)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.execute(
                    select(WebhookDeliveryModel).where(
                        WebhookDeliveryModel.workflow_run_id == workflow_run_id,
                        WebhookDeliveryModel.webhook_node_id == webhook_node_id,
                    )
                )
                row = existing.scalar_one_or_none()
                if row is not None:
                    return row, False
                # The violation was not the run+node uniqueness -- re-raise.
                raise
            await session.refresh(delivery)
            return delivery, True

    async def get_webhook_delivery(
        self, delivery_id: int
    ) -> Optional[WebhookDeliveryModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(WebhookDeliveryModel).where(
                    WebhookDeliveryModel.id == delivery_id
                )
            )
            return result.scalar_one_or_none()

    async def claim_webhook_delivery(
        self, delivery_id: int, lease_seconds: int
    ) -> Optional[WebhookDeliveryModel]:
        """Atomically claim a pending, due delivery for one worker to process.

        A conditional UPDATE pushes ``scheduled_for`` out by a short lease. Only
        one concurrent worker can win -- the others re-evaluate the WHERE after
        the first commits, see the future ``scheduled_for``, match nothing, and
        get ``None``. This prevents the non-atomic ``status == 'pending'`` read
        from letting two workers double-send the same delivery. If the winning
        worker crashes mid-send, the lease expires and the sweeper re-enqueues it.

        Returns the claimed row, or ``None`` if it was not claimable (already
        claimed, not pending, or not yet due).
        """
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        async with self.async_session() as session:
            result = await session.execute(
                update(WebhookDeliveryModel)
                .where(
                    WebhookDeliveryModel.id == delivery_id,
                    WebhookDeliveryModel.status == "pending",
                    or_(
                        WebhookDeliveryModel.scheduled_for.is_(None),
                        WebhookDeliveryModel.scheduled_for <= now,
                    ),
                )
                .values(scheduled_for=lease_until, updated_at=now)
            )
            await session.commit()
            if result.rowcount == 0:
                return None
            fetched = await session.execute(
                select(WebhookDeliveryModel).where(
                    WebhookDeliveryModel.id == delivery_id
                )
            )
            return fetched.scalar_one_or_none()

    async def mark_webhook_delivery_succeeded(
        self, delivery_id: int, attempt_count: int, status_code: Optional[int]
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(WebhookDeliveryModel)
                .where(WebhookDeliveryModel.id == delivery_id)
                .values(
                    status="succeeded",
                    attempt_count=attempt_count,
                    last_status_code=status_code,
                    last_error=None,
                    scheduled_for=None,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def schedule_webhook_delivery_retry(
        self,
        delivery_id: int,
        attempt_count: int,
        scheduled_for: datetime,
        last_error: str,
        last_status_code: Optional[int],
    ) -> None:
        """Record a transient failure and set when the next attempt is due."""
        async with self.async_session() as session:
            await session.execute(
                update(WebhookDeliveryModel)
                .where(WebhookDeliveryModel.id == delivery_id)
                .values(
                    status="pending",
                    attempt_count=attempt_count,
                    scheduled_for=scheduled_for,
                    last_error=last_error[:2000] if last_error else last_error,
                    last_status_code=last_status_code,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def mark_webhook_delivery_dead_letter(
        self,
        delivery_id: int,
        attempt_count: int,
        last_error: str,
        last_status_code: Optional[int],
    ) -> None:
        """Terminal failure: parked for inspection, never retried again."""
        async with self.async_session() as session:
            await session.execute(
                update(WebhookDeliveryModel)
                .where(WebhookDeliveryModel.id == delivery_id)
                .values(
                    status="dead_letter",
                    attempt_count=attempt_count,
                    last_error=last_error[:2000] if last_error else last_error,
                    last_status_code=last_status_code,
                    scheduled_for=None,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            logger.warning(
                f"Webhook delivery {delivery_id} dead-lettered after "
                f"{attempt_count} attempts: {last_error}"
            )

    async def get_due_webhook_deliveries(
        self, now: Optional[datetime] = None, limit: int = 100, after_id: int = 0
    ) -> List[WebhookDeliveryModel]:
        """One page of pending deliveries whose next attempt is due.

        Used by the periodic sweeper to re-enqueue deliveries whose ARQ job was
        lost (worker restart, Redis flush). The delivery task is idempotent, so a
        spurious re-enqueue is harmless. Ordered by ``id`` and gated on
        ``after_id`` for keyset pagination -- re-enqueuing does not change a row's
        due state, so the sweeper pages by id to drain the whole backlog instead
        of re-reading the same first page forever.
        """
        cutoff = now or datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                select(WebhookDeliveryModel)
                .where(
                    WebhookDeliveryModel.status == "pending",
                    WebhookDeliveryModel.scheduled_for.isnot(None),
                    WebhookDeliveryModel.scheduled_for <= cutoff,
                    WebhookDeliveryModel.id > after_id,
                )
                .order_by(WebhookDeliveryModel.id)
                .limit(limit)
            )
            return list(result.scalars().all())
