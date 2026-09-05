"""Database access for telephony trunks.

A trunk is one carrier path on a telephony configuration. Phone numbers point
at the trunk they are authorised on, so outbound calls present a caller ID the
carrier actually owns.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import TelephonyPhoneNumberModel, TelephonyTrunkModel


class TelephonyTrunkConflictError(Exception):
    """Raised when a trunk violates a DB constraint."""


class TelephonyTrunkClient(BaseDBClient):
    async def list_trunks_for_config(
        self, telephony_configuration_id: int
    ) -> List[TelephonyTrunkModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyTrunkModel)
                .where(
                    TelephonyTrunkModel.telephony_configuration_id
                    == telephony_configuration_id
                )
                .order_by(TelephonyTrunkModel.id)
            )
            return list(result.scalars().all())

    async def get_trunk_for_config(
        self, trunk_id: int, telephony_configuration_id: int
    ) -> Optional[TelephonyTrunkModel]:
        """Scoped by configuration so a caller cannot reach another org's trunk
        by guessing an id."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyTrunkModel).where(
                    TelephonyTrunkModel.id == trunk_id,
                    TelephonyTrunkModel.telephony_configuration_id
                    == telephony_configuration_id,
                )
            )
            return result.scalar_one_or_none()

    async def create_trunk(
        self,
        *,
        telephony_configuration_id: int,
        name: str,
        enabled: bool = True,
        settings: Optional[Dict[str, Any]] = None,
        external_id: Optional[str] = None,
    ) -> TelephonyTrunkModel:
        async with self.async_session() as session:
            row = TelephonyTrunkModel(
                telephony_configuration_id=telephony_configuration_id,
                name=name,
                enabled=enabled,
                settings=settings or {},
                external_id=external_id,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise TelephonyTrunkConflictError(str(e.orig)) from e
            await session.refresh(row)
            return row

    async def update_trunk(
        self,
        *,
        trunk_id: int,
        telephony_configuration_id: int,
        name: Optional[str] = None,
        enabled: Optional[bool] = None,
        settings: Optional[Dict[str, Any]] = None,
        external_id: Optional[str] = None,
    ) -> Optional[TelephonyTrunkModel]:
        """Partial update: only the fields passed as non-None are written.

        ``external_id`` is server-managed — it is cleared by passing an empty
        string, never by omitting it, so a plain rename cannot orphan the
        provider-side trunk.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyTrunkModel).where(
                    TelephonyTrunkModel.id == trunk_id,
                    TelephonyTrunkModel.telephony_configuration_id
                    == telephony_configuration_id,
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                return None

            if name is not None:
                row.name = name
            if enabled is not None:
                row.enabled = enabled
            if settings is not None:
                row.settings = settings
            if external_id is not None:
                row.external_id = external_id or None

            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise TelephonyTrunkConflictError(str(e.orig)) from e
            await session.refresh(row)
            return row

    async def delete_trunk(
        self, trunk_id: int, telephony_configuration_id: int
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyTrunkModel).where(
                    TelephonyTrunkModel.id == trunk_id,
                    TelephonyTrunkModel.telephony_configuration_id
                    == telephony_configuration_id,
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def count_phone_numbers_for_trunk(self, trunk_id: int) -> int:
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(TelephonyPhoneNumberModel)
                .where(TelephonyPhoneNumberModel.telephony_trunk_id == trunk_id)
            )
            return int(result.scalar_one())

    async def get_trunk_ids_by_address_for_config(
        self, telephony_configuration_id: int
    ) -> Dict[str, Optional[int]]:
        """Active number -> the trunk it dials out over, for the call path.

        Keyed by normalized address because that is what the caller-ID
        selection returns.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(
                    TelephonyPhoneNumberModel.address_normalized,
                    TelephonyPhoneNumberModel.telephony_trunk_id,
                ).where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                )
            )
            return {address: trunk_id for address, trunk_id in result.all()}
