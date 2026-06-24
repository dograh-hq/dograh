from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from api.constants import DEFAULT_FREE_CALL_SECONDS
from api.db.base_client import BaseDBClient
from api.db.models import (
    APIKeyModel,
    OrganizationModel,
    UserModel,
    organization_users_association,
)
from api.utils.api_key import generate_api_key

# Sentinel so update_organization_voicelink can distinguish "not passed"
# from an explicit None (used to clear a field).
_UNSET = object()


class OrganizationClient(BaseDBClient):
    async def get_organization_by_id(
        self, organization_id: int
    ) -> Optional[OrganizationModel]:
        """Get an organization by its ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            return result.scalars().first()

    async def get_free_call_seconds_remaining(
        self, organization_id: int
    ) -> Optional[int]:
        """Remaining trial call seconds, or None when unmetered (unlimited)."""
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganizationModel.free_call_seconds_remaining).where(
                    OrganizationModel.id == organization_id
                )
            )
            return result.scalar_one_or_none()

    async def decrement_free_call_seconds(
        self, organization_id: int, seconds: int
    ) -> None:
        """Subtract `seconds` from the org's trial balance (floored at 0).

        No-op for unmetered (NULL) orgs or a non-positive `seconds`.
        """
        if seconds <= 0:
            return
        async with self.async_session() as session:
            await session.execute(
                update(OrganizationModel)
                .where(
                    OrganizationModel.id == organization_id,
                    OrganizationModel.free_call_seconds_remaining.isnot(None),
                )
                .values(
                    free_call_seconds_remaining=func.greatest(
                        OrganizationModel.free_call_seconds_remaining - seconds, 0
                    )
                )
            )
            await session.commit()

    async def add_call_seconds(
        self, organization_id: int, seconds: int
    ) -> Optional[int]:
        """Top up the org's call-seconds balance; returns the new balance.

        COALESCE(.,0)+seconds so a depleted (0) trial balance tops up. Callers MUST
        guard unmetered (NULL) orgs — crediting NULL would convert them to metered.
        """
        if seconds <= 0:
            return await self.get_free_call_seconds_remaining(organization_id)
        async with self.async_session() as session:
            await session.execute(
                update(OrganizationModel)
                .where(OrganizationModel.id == organization_id)
                .values(
                    free_call_seconds_remaining=func.coalesce(
                        OrganizationModel.free_call_seconds_remaining, 0
                    )
                    + seconds
                )
            )
            await session.commit()
        return await self.get_free_call_seconds_remaining(organization_id)

    async def get_or_create_organization_by_provider_id(
        self, org_provider_id: str, user_id: int
    ) -> tuple[OrganizationModel, bool]:
        """Get an existing organization by provider_id or create a new one.

        Returns:
            A tuple of (organization, was_created) where was_created is True if the organization
            was created in this call, False if it already existed.
        """
        async with self.async_session() as session:
            # First try to get existing organization
            result = await session.execute(
                select(OrganizationModel).where(
                    OrganizationModel.provider_id == org_provider_id
                )
            )
            organization = result.scalars().first()

            if organization is None:
                # Use PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
                # This is atomic and handles race conditions at the database level

                stmt = insert(OrganizationModel.__table__).values(
                    provider_id=org_provider_id,
                    created_at=datetime.now(timezone.utc),
                    # Trial grant for brand-new orgs (NULL would mean unlimited).
                    free_call_seconds_remaining=(
                        DEFAULT_FREE_CALL_SECONDS if DEFAULT_FREE_CALL_SECONDS > 0 else None
                    ),
                )
                # ON CONFLICT DO NOTHING - if another request already inserted, this becomes a no-op
                stmt = stmt.on_conflict_do_nothing(index_elements=["provider_id"])

                result = await session.execute(stmt)
                await session.commit()

                # Check if we actually inserted (rowcount > 0) or if there was a conflict (rowcount == 0)
                was_created = result.rowcount > 0

                # Now fetch the organization (either the one we just created or the one that existed)
                result = await session.execute(
                    select(OrganizationModel).where(
                        OrganizationModel.provider_id == org_provider_id
                    )
                )
                organization = result.scalars().first()

                if organization is None:
                    # This should never happen, but handle it just in case
                    error_msg = f"Failed to create or fetch organization with provider_id {org_provider_id}"
                    raise ValueError(error_msg)

                # Only create API key if we actually created the organization
                if was_created:
                    # Create a default API key for the new organization
                    _, key_hash, key_prefix = generate_api_key()

                    api_key = APIKeyModel(
                        organization_id=organization.id,
                        name="Default API Key",
                        key_hash=key_hash,
                        key_prefix=key_prefix,
                        is_active=True,
                        created_by=user_id,
                    )
                    session.add(api_key)
                    await session.commit()

                await session.refresh(organization)
                return organization, was_created
            return organization, False

    async def get_organization_with_users(
        self, organization_id: int
    ) -> Optional[OrganizationModel]:
        """Get an organization with its member users eagerly loaded."""
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganizationModel)
                .options(selectinload(OrganizationModel.users))
                .where(OrganizationModel.id == organization_id)
            )
            return result.scalars().first()

    async def list_organizations_with_users(
        self, exclude_user_id: Optional[int] = None
    ) -> List[OrganizationModel]:
        """List organizations with member users eagerly loaded.

        When ``exclude_user_id`` is given, organizations that the user belongs
        to are omitted (used by the admin clients view to hide the superuser's
        own organization).
        """
        async with self.async_session() as session:
            query = (
                select(OrganizationModel)
                .options(selectinload(OrganizationModel.users))
                .order_by(OrganizationModel.created_at.desc())
            )
            if exclude_user_id is not None:
                query = query.where(
                    ~OrganizationModel.users.any(UserModel.id == exclude_user_id)
                )
            result = await session.execute(query)
            return list(result.scalars().all())

    async def update_organization_voicelink(
        self,
        organization_id: int,
        *,
        client_id=_UNSET,
        username=_UNSET,
        status=_UNSET,
        error=_UNSET,
        provision_secret=_UNSET,
    ) -> Optional[OrganizationModel]:
        """Partially update the org's VoiceLink provisioning fields.

        Only the keyword arguments that are passed are written; pass an
        explicit ``None`` to clear a field.
        """
        async with self.async_session() as session:
            organization = await session.get(OrganizationModel, organization_id)
            if organization is None:
                return None

            if client_id is not _UNSET:
                organization.voicelink_client_id = client_id
            if username is not _UNSET:
                organization.voicelink_username = username
            if status is not _UNSET:
                organization.voicelink_status = status
            if error is not _UNSET:
                organization.voicelink_error = error
            if provision_secret is not _UNSET:
                organization.voicelink_provision_secret = provision_secret

            await session.commit()
            await session.refresh(organization)
            return organization

    async def add_user_to_organization(
        self, user_id: int, organization_id: int
    ) -> None:
        """Ensure that a user is linked to an organization (many-to-many).

        The association is created only if it does not already exist.
        Uses INSERT ... ON CONFLICT DO NOTHING to handle race conditions.
        """
        async with self.async_session() as session:
            # Use PostgreSQL's INSERT ... ON CONFLICT DO NOTHING
            # This handles race conditions at the database level

            stmt = insert(organization_users_association).values(
                user_id=user_id, organization_id=organization_id
            )
            # ON CONFLICT DO NOTHING - if another request already inserted, this becomes a no-op
            # The primary key constraint on (user_id, organization_id) will trigger the conflict
            stmt = stmt.on_conflict_do_nothing()

            await session.execute(stmt)
            await session.commit()
