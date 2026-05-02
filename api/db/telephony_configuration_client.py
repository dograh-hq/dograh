"""Database access for telephony configurations.

Each row represents one provider account that an organization has connected
(e.g. "Twilio US prod", "Vobiz IN sandbox"). Replaces the single-row-per-org
``OrganizationConfiguration(TELEPHONY_CONFIGURATION)`` storage.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import CampaignModel, TelephonyConfigurationModel


class TelephonyConfigurationInUseError(Exception):
    """Raised when deleting a config that is still referenced by a campaign."""


class TelephonyConfigurationClient(BaseDBClient):
    async def list_telephony_configurations(
        self, organization_id: int
    ) -> List[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel)
                .where(TelephonyConfigurationModel.organization_id == organization_id)
                .order_by(TelephonyConfigurationModel.created_at)
            )
            return list(result.scalars().all())

    async def get_telephony_configuration(
        self, config_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            return await session.get(TelephonyConfigurationModel, config_id)

    async def get_telephony_configuration_for_org(
        self, config_id: int, organization_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        """Lookup scoped to an org — used to authorize per-org access."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.id == config_id,
                    TelephonyConfigurationModel.organization_id == organization_id,
                )
            )
            return result.scalars().first()

    async def get_default_telephony_configuration(
        self, organization_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.is_default_outbound.is_(True),
                )
            )
            return result.scalars().first()

    async def list_telephony_configurations_by_provider(
        self, organization_id: int, provider: str
    ) -> List[TelephonyConfigurationModel]:
        """Used by inbound matching to enumerate candidates of a given provider."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.provider == provider,
                )
            )
            return list(result.scalars().all())

    async def list_all_telephony_configurations_by_provider(
        self, provider: str
    ) -> List[TelephonyConfigurationModel]:
        """List configs of a given provider across every organization.

        Used by background workers like the ARI manager that maintain
        long-lived connections per config row, independent of any one org.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.provider == provider,
                )
            )
            return list(result.scalars().all())

    async def create_telephony_configuration(
        self,
        organization_id: int,
        name: str,
        provider: str,
        credentials: Dict[str, Any],
        is_default_outbound: bool = False,
    ) -> TelephonyConfigurationModel:
        """Create a new config row. Duplicate-account guarding is the caller's
        responsibility; this method does not enforce it."""
        async with self.async_session() as session:
            existing_count = await session.scalar(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                )
            )
            if existing_count == 0:
                is_default_outbound = True
            elif is_default_outbound:
                await self._clear_default_outbound(session, organization_id)

            row = TelephonyConfigurationModel(
                organization_id=organization_id,
                name=name,
                provider=provider,
                credentials=credentials,
                is_default_outbound=is_default_outbound,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise e
            await session.refresh(row)
            return row

    async def update_telephony_configuration(
        self,
        config_id: int,
        organization_id: int,
        name: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
    ) -> Optional[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return None

            if name is not None:
                row.name = name
            if credentials is not None:
                row.credentials = credentials

            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise e
            await session.refresh(row)
            return row

    async def set_default_telephony_configuration(
        self, config_id: int, organization_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        """Mark this config as the org's default outbound, clearing any other default."""
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return None
            await self._clear_default_outbound(session, organization_id)
            row.is_default_outbound = True
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_telephony_configuration(
        self, config_id: int, organization_id: int
    ) -> bool:
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return False

            campaign_ref = await session.execute(
                select(CampaignModel.id)
                .where(CampaignModel.telephony_configuration_id == config_id)
                .limit(1)
            )
            if campaign_ref.first():
                raise TelephonyConfigurationInUseError(
                    f"Telephony configuration {config_id} is referenced by one or "
                    f"more campaigns and cannot be deleted."
                )

            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    async def _clear_default_outbound(session, organization_id: int) -> None:
        await session.execute(
            update(TelephonyConfigurationModel)
            .where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.is_default_outbound.is_(True),
            )
            .values(is_default_outbound=False)
        )
