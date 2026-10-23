"""Database client for inbound SMS conversations and the MessageSid ledger."""

from datetime import UTC, datetime
from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import SmsConversationModel, SmsInboundMessageModel


class SmsConversationClient(BaseDBClient):
    """Phone-number -> active workflow run mapping plus idempotency ledger."""

    async def get_active_sms_conversation(
        self, *, phone: str, to_number: str, now: Optional[datetime] = None
    ) -> Optional[SmsConversationModel]:
        """Newest non-expired conversation for ``phone`` on ``to_number``."""
        now = now or datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                select(SmsConversationModel)
                .where(SmsConversationModel.phone == phone)
                .where(SmsConversationModel.to_number == to_number)
                .where(SmsConversationModel.expires_at > now)
                .order_by(SmsConversationModel.id.desc())
                .limit(1)
            )
            return result.scalars().first()

    async def create_sms_conversation(
        self,
        *,
        phone: str,
        to_number: str,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        expires_at: datetime,
    ) -> SmsConversationModel:
        async with self.async_session() as session:
            now = datetime.now(UTC)
            conversation = SmsConversationModel(
                phone=phone,
                to_number=to_number,
                workflow_id=workflow_id,
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                created_at=now,
                last_activity_at=now,
                expires_at=expires_at,
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            logger.info(
                f"Created SMS conversation id={conversation.id} "
                f"run={workflow_run_id} workflow={workflow_id}"
            )
            return conversation

    async def touch_sms_conversation(
        self, conversation_id: int, *, last_activity_at: datetime, expires_at: datetime
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(SmsConversationModel)
                .where(SmsConversationModel.id == conversation_id)
                .values(last_activity_at=last_activity_at, expires_at=expires_at)
            )
            await session.commit()

    async def expire_sms_conversation(
        self, conversation_id: int, *, now: Optional[datetime] = None
    ) -> None:
        """Retire a mapping row so the next SMS from that sender starts fresh."""
        now = now or datetime.now(UTC)
        async with self.async_session() as session:
            await session.execute(
                update(SmsConversationModel)
                .where(SmsConversationModel.id == conversation_id)
                .values(expires_at=now)
            )
            await session.commit()

    async def claim_sms_inbound_message(
        self, *, message_sid: str, from_number: str, to_number: str
    ) -> Tuple[bool, SmsInboundMessageModel]:
        """Atomically claim ``message_sid``.

        Returns ``(True, row)`` when this call inserted the row (first
        delivery) and ``(False, existing_row)`` on a duplicate webhook. The
        unique constraint on ``message_sid`` makes the claim race-safe across
        workers.
        """
        async with self.async_session() as session:
            stmt = (
                pg_insert(SmsInboundMessageModel)
                .values(
                    message_sid=message_sid,
                    from_number=from_number,
                    to_number=to_number,
                    status="processing",
                    received_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["message_sid"])
                .returning(SmsInboundMessageModel.id)
            )
            inserted_id = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()

            result = await session.execute(
                select(SmsInboundMessageModel).where(
                    SmsInboundMessageModel.message_sid == message_sid
                )
            )
            row = result.scalar_one()
            return inserted_id is not None, row

    async def finalize_sms_inbound_message(
        self,
        *,
        message_sid: str,
        status: str,
        response_text: Optional[str] = None,
        conversation_id: Optional[int] = None,
        workflow_run_id: Optional[int] = None,
    ) -> None:
        values = {"status": status, "completed_at": datetime.now(UTC)}
        if response_text is not None:
            values["response_text"] = response_text
        if conversation_id is not None:
            values["conversation_id"] = conversation_id
        if workflow_run_id is not None:
            values["workflow_run_id"] = workflow_run_id
        async with self.async_session() as session:
            await session.execute(
                update(SmsInboundMessageModel)
                .where(SmsInboundMessageModel.message_sid == message_sid)
                .values(**values)
            )
            await session.commit()
