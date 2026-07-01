"""DB client for Razorpay top-up transactions."""

from datetime import UTC, datetime
from typing import List, Optional

from sqlalchemy import select, update

from api.db.base_client import BaseDBClient
from api.db.models import PaymentTransactionModel


class PaymentClient(BaseDBClient):
    async def create_transaction(
        self,
        *,
        organization_id: int,
        created_by: Optional[int],
        razorpay_order_id: str,
        pack_id: Optional[str],
        seconds: int,
        amount_paise: int,
    ) -> PaymentTransactionModel:
        async with self.async_session() as session:
            txn = PaymentTransactionModel(
                organization_id=organization_id,
                created_by=created_by,
                razorpay_order_id=razorpay_order_id,
                pack_id=pack_id,
                seconds=seconds,
                amount_paise=amount_paise,
                status="created",
            )
            session.add(txn)
            await session.commit()
            await session.refresh(txn)
            return txn

    async def get_transaction_by_order_id(
        self, razorpay_order_id: str, organization_id: int
    ) -> Optional[PaymentTransactionModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentTransactionModel).where(
                    PaymentTransactionModel.razorpay_order_id == razorpay_order_id,
                    PaymentTransactionModel.organization_id == organization_id,
                )
            )
            return result.scalars().first()

    async def get_transaction_by_order_id_unscoped(
        self, razorpay_order_id: str
    ) -> Optional[PaymentTransactionModel]:
        """Look up a txn by its globally-unique gateway order id, without org
        scoping — for gateway callbacks (e.g. PayU surl/furl) that carry no
        authenticated session. The org to credit is read off the row itself.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentTransactionModel).where(
                    PaymentTransactionModel.razorpay_order_id == razorpay_order_id
                )
            )
            return result.scalars().first()

    async def mark_transaction_paid(
        self, razorpay_order_id: str, razorpay_payment_id: str
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(PaymentTransactionModel)
                .where(PaymentTransactionModel.razorpay_order_id == razorpay_order_id)
                .values(
                    status="paid",
                    razorpay_payment_id=razorpay_payment_id,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def get_paid_pack_ids(self, organization_id: int) -> set[str]:
        """Distinct pack_ids the org has successfully paid for (drives plan tier)."""
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentTransactionModel.pack_id).where(
                    PaymentTransactionModel.organization_id == organization_id,
                    PaymentTransactionModel.status == "paid",
                )
            )
            return {pid for pid in result.scalars().all() if pid}

    async def list_transactions(
        self, organization_id: int, limit: int = 20
    ) -> List[PaymentTransactionModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentTransactionModel)
                .where(PaymentTransactionModel.organization_id == organization_id)
                .order_by(PaymentTransactionModel.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
