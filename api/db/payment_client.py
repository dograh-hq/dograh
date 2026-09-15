from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.future import select
from loguru import logger

from api.db.base_client import BaseDBClient
from api.db.models import OrganizationModel, PaymentTransactionModel


class PaymentClient(BaseDBClient):
    """Database client for payment and wallet recharge transactions."""

    _table_checked: bool = False

    async def _ensure_table(self):
        """Ensure payment_transactions table exists if migrations have not run."""
        if PaymentClient._table_checked:
            return
        statements = [
            """
            CREATE TABLE IF NOT EXISTS payment_transactions (
                id SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                amount_usd DOUBLE PRECISION NOT NULL,
                amount_inr DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                currency VARCHAR(8) NOT NULL DEFAULT 'USD',
                receipt VARCHAR(64) NOT NULL UNIQUE,
                razorpay_order_id VARCHAR(64) NOT NULL,
                razorpay_payment_id VARCHAR(64),
                razorpay_signature VARCHAR(256),
                status VARCHAR(32) NOT NULL DEFAULT 'created',
                notes JSON DEFAULT '{}'::json,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_payment_transactions_org_status ON payment_transactions (organization_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_payment_transactions_order_id ON payment_transactions (razorpay_order_id)",
            "CREATE INDEX IF NOT EXISTS ix_payment_transactions_payment_id ON payment_transactions (razorpay_payment_id)",
        ]
        try:
            from sqlalchemy import text
            async with self.async_session() as session:
                for stmt in statements:
                    await session.execute(text(stmt))
                await session.commit()
            PaymentClient._table_checked = True
            logger.info("payment_transactions table ensured successfully")
        except Exception as exc:
            logger.warning(f"Could not auto-ensure payment_transactions table: {exc}")


    async def create_transaction(
        self,
        organization_id: int,
        user_id: Optional[int],
        amount_usd: float,
        amount_inr: float,
        currency: str,
        receipt: str,
        razorpay_order_id: str,
        notes: Optional[dict] = None,
    ) -> PaymentTransactionModel:
        """Create a new pending payment transaction record."""
        await self._ensure_table()
        async with self.async_session() as session:

            tx = PaymentTransactionModel(
                organization_id=organization_id,
                user_id=user_id,
                amount_usd=amount_usd,
                amount_inr=amount_inr,
                currency=currency,
                receipt=receipt,
                razorpay_order_id=razorpay_order_id,
                status="created",
                notes=notes or {},
            )
            session.add(tx)
            await session.commit()
            await session.refresh(tx)
            return tx

    async def get_transaction_by_order_id(
        self, razorpay_order_id: str
    ) -> Optional[PaymentTransactionModel]:
        """Fetch transaction by Razorpay order ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentTransactionModel).where(
                    PaymentTransactionModel.razorpay_order_id == razorpay_order_id
                )
            )
            return result.scalars().first()

    async def get_transaction_by_receipt(
        self, receipt: str
    ) -> Optional[PaymentTransactionModel]:
        """Fetch transaction by receipt identifier."""
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentTransactionModel).where(
                    PaymentTransactionModel.receipt == receipt
                )
            )
            return result.scalars().first()

    async def mark_transaction_paid_and_credit_wallet(
        self,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: Optional[str] = None,
    ) -> Tuple[bool, float, Optional[PaymentTransactionModel]]:
        """Idempotently mark a transaction as paid and credit the organization's wallet balance.

        Returns:
            Tuple of (was_newly_credited, new_balance_usd, transaction)
        """
        async with self.async_session() as session:
            # 1. Fetch transaction
            result = await session.execute(
                select(PaymentTransactionModel).where(
                    PaymentTransactionModel.razorpay_order_id == razorpay_order_id
                )
            )
            tx = result.scalars().first()
            if not tx:
                raise ValueError(f"Transaction for order {razorpay_order_id} not found")

            # 2. Check if already marked paid (idempotency check)
            if tx.status == "paid":
                # Already processed and credited
                org_result = await session.execute(
                    select(OrganizationModel).where(
                        OrganizationModel.id == tx.organization_id
                    )
                )
                org = org_result.scalars().first()
                current_bal = float(org.wallet_balance_usd or 0.0) if org else 0.0
                return False, current_bal, tx

            # 3. Mark as paid
            tx.status = "paid"
            tx.razorpay_payment_id = razorpay_payment_id
            if razorpay_signature:
                tx.razorpay_signature = razorpay_signature
            tx.updated_at = datetime.now(timezone.utc)
            session.add(tx)

            # 4. Atomically credit organization wallet balance
            org_result = await session.execute(
                select(OrganizationModel).where(
                    OrganizationModel.id == tx.organization_id
                )
            )
            org = org_result.scalars().first()
            if not org:
                raise ValueError(f"Organization {tx.organization_id} not found")

            current_bal = float(org.wallet_balance_usd or 0.0)
            new_bal = round(current_bal + tx.amount_usd, 4)
            org.wallet_balance_usd = new_bal
            session.add(org)

            await session.commit()
            await session.refresh(tx)
            await session.refresh(org)

            logger.info(
                f"[Razorpay] Successfully credited ${tx.amount_usd:.2f} to Org #{tx.organization_id} "
                f"(Receipt: {tx.receipt}, Payment ID: {razorpay_payment_id}). New balance: ${new_bal:.2f}"
            )
            return True, float(org.wallet_balance_usd), tx

    async def mark_transaction_failed(
        self, razorpay_order_id: str, reason: Optional[str] = None
    ) -> Optional[PaymentTransactionModel]:
        """Mark a transaction as failed."""
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentTransactionModel).where(
                    PaymentTransactionModel.razorpay_order_id == razorpay_order_id
                )
            )
            tx = result.scalars().first()
            if not tx or tx.status == "paid":
                return tx

            tx.status = "failed"
            if reason and isinstance(tx.notes, dict):
                tx.notes["failure_reason"] = reason
            tx.updated_at = datetime.now(timezone.utc)
            session.add(tx)
            await session.commit()
            await session.refresh(tx)
            return tx

    async def list_organization_transactions(
        self, organization_id: int, limit: int = 20
    ) -> list[PaymentTransactionModel]:
        """List payment transactions for an organization in descending chronological order."""
        await self._ensure_table()
        async with self.async_session() as session:
            result = await session.execute(
                select(PaymentTransactionModel)
                .where(PaymentTransactionModel.organization_id == organization_id)
                .order_by(PaymentTransactionModel.id.desc())
                .limit(limit)
            )
            return list(result.scalars().all())


payment_client = PaymentClient()
