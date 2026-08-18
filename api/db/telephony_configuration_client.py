"""Database access for telephony configurations.

Each row represents one provider account that an organization has connected
(e.g. "Twilio US prod", "Vobiz IN sandbox"). Replaces the single-row-per-org
``OrganizationConfiguration(TELEPHONY_CONFIGURATION)`` storage.
"""

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import CampaignModel, TelephonyConfigurationModel


class TelephonyConfigurationInUseError(Exception):
    """Raised when deleting a config that is still referenced by a campaign."""


class TelephonyConfigurationConflictError(Exception):
    """Raised when a telephony configuration violates a DB constraint."""


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

    async def list_outbound_telephony_configuration_candidates(
        self, organization_id: int
    ) -> List[TelephonyConfigurationModel]:
        """Active outbound candidates, with an explicit default considered first."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel)
                .where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.inactive.is_(False),
                )
                .order_by(
                    TelephonyConfigurationModel.is_default_outbound.desc(),
                    TelephonyConfigurationModel.created_at,
                    TelephonyConfigurationModel.id,
                )
            )
            return list(result.scalars().all())

    async def get_telephony_configuration(
        self, config_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            return await session.get(TelephonyConfigurationModel, config_id)

    async def get_telephony_configuration_for_org(
        self,
        config_id: int,
        organization_id: int,
        active_only: bool = True,
    ) -> Optional[TelephonyConfigurationModel]:
        """Lookup scoped to an org, excluding parked configs by default.

        Management flows that need to display, repair, or reactivate a parked
        row must opt in with ``active_only=False``.
        """
        async with self.async_session() as session:
            query = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.id == config_id,
                TelephonyConfigurationModel.organization_id == organization_id,
            )
            if active_only:
                query = query.where(TelephonyConfigurationModel.inactive.is_(False))
            result = await session.execute(query)
            return result.scalars().first()

    async def get_default_telephony_configuration(
        self, organization_id: int, active_only: bool = True
    ) -> Optional[TelephonyConfigurationModel]:
        """Return the default outbound config, if it is usable for routing."""
        async with self.async_session() as session:
            query = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.is_default_outbound.is_(True),
            )
            if active_only:
                query = query.where(TelephonyConfigurationModel.inactive.is_(False))
            result = await session.execute(query)
            return result.scalars().first()

    async def list_telephony_configurations_by_provider(
        self, organization_id: int, provider: str, active_only: bool = True
    ) -> List[TelephonyConfigurationModel]:
        """List provider configs usable for inbound matching by default."""
        async with self.async_session() as session:
            query = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.provider == provider,
            )
            if active_only:
                query = query.where(TelephonyConfigurationModel.inactive.is_(False))
            result = await session.execute(query)
            return list(result.scalars().all())

    async def count_telnyx_configs_missing_webhook_public_key(
        self, organization_id: int
    ) -> int:
        """Count Telnyx configs in this org with no webhook_public_key in credentials.

        Used by the org-warnings endpoint to surface a UI nudge until customers
        paste their portal-issued public key.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.provider == "telnyx",
                    (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "webhook_public_key"
                        ).is_(None)
                    )
                    | (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "webhook_public_key"
                        )
                        == ""
                    ),
                )
            )
            return int(result.scalar() or 0)

    async def count_vonage_configs_missing_signature_secret(
        self, organization_id: int
    ) -> int:
        """Count Vonage configs in this org with no signature_secret."""
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.provider == "vonage",
                    (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "signature_secret"
                        ).is_(None)
                    )
                    | (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "signature_secret"
                        )
                        == ""
                    ),
                )
            )
            return int(result.scalar() or 0)

    async def list_active_telephony_configurations_by_provider(
        self, provider: str
    ) -> List[TelephonyConfigurationModel]:
        """List the non-deactivated configs of a given provider, across all orgs.

        Used by background workers like the ARI manager that maintain
        long-lived connections per config row, independent of any one org.
        Deactivated rows stay excluded until someone reactivates them.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyConfigurationModel.inactive.is_(False),
                )
            )
            return list(result.scalars().all())

    async def set_telephony_configuration_inactive(
        self, config_id: int, organization_id: int, reason: str
    ) -> bool:
        """Deactivate a config, recording when and why."""
        async with self.async_session() as session:
            result = await session.execute(
                update(TelephonyConfigurationModel)
                .where(
                    TelephonyConfigurationModel.id == config_id,
                    TelephonyConfigurationModel.organization_id == organization_id,
                )
                .values(
                    inactive=True,
                    inactive_since=datetime.now(UTC),
                    inactive_reason=reason[:255],
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def set_telephony_configuration_active(
        self, config_id: int, organization_id: int
    ) -> bool:
        """Clear the inactive flag and the recorded deactivation details."""
        async with self.async_session() as session:
            result = await session.execute(
                update(TelephonyConfigurationModel)
                .where(
                    TelephonyConfigurationModel.id == config_id,
                    TelephonyConfigurationModel.organization_id == organization_id,
                )
                .values(
                    inactive=False,
                    inactive_since=None,
                    inactive_reason=None,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def create_telephony_configuration(
        self,
        organization_id: int,
        name: str,
        provider: str,
        credentials: Dict[str, Any],
        is_default_outbound: bool = False,
    ) -> TelephonyConfigurationModel:
        """Create a new config row. Duplicate-account guarding is the caller's
        responsibility; this method does not enforce it.

        Which configuration is the default outbound is the customer's choice,
        so this stores exactly what the caller passed. The only write beyond
        the new row is demoting the previous default when this one claims it —
        part of honouring ``is_default_outbound=True``, since two defaults in
        one organization would make ``get_default_telephony_configuration``
        return an arbitrary row.
        """
        async with self.async_session() as session:
            if is_default_outbound:
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
                raise TelephonyConfigurationConflictError(str(e)) from e
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
                raise TelephonyConfigurationConflictError(str(e)) from e
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
