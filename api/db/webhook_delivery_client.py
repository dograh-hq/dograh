"""Database client for durable outbound webhook deliveries.

Persists one row per webhook node per workflow run and exposes the state
transitions the delivery task and sweeper need: create (pending), succeed,
schedule the next retry, and park as dead-letter. Mirrors the campaign retry
pattern -- the row is the source of truth, ``scheduled_for`` gates due work.
"""

from datetime import UTC, datetime
from typing import List, Optional

from loguru import logger
from sqlalchemy import select, update

from api.db.base_client import BaseDBClient
from api.db.models import WebhookDeliveryModel


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
        scheduled_for: Optional[datetime] = None,
    ) -> WebhookDeliveryModel:
        """Create a ``pending`` delivery row, due immediately by default."""
        async with self.async_session() as session:
            delivery = WebhookDeliveryModel(
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                webhook_name=webhook_name,
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
            await session.commit()
            await session.refresh(delivery)
            return delivery

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
        self, now: Optional[datetime] = None, limit: int = 100
    ) -> List[WebhookDeliveryModel]:
        """Pending deliveries whose next attempt is due.

        Used by the periodic sweeper to re-enqueue deliveries whose ARQ job was
        lost (worker restart, Redis flush). The delivery task is idempotent, so a
        spurious re-enqueue is harmless.
        """
        cutoff = now or datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                select(WebhookDeliveryModel)
                .where(
                    WebhookDeliveryModel.status == "pending",
                    WebhookDeliveryModel.scheduled_for.isnot(None),
                    WebhookDeliveryModel.scheduled_for <= cutoff,
                )
                .order_by(WebhookDeliveryModel.scheduled_for)
                .limit(limit)
            )
            return list(result.scalars().all())
