from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import APIKeyModel
from api.utils.api_key import generate_api_key, hash_api_key


class APIKeyClient(BaseDBClient):
    async def create_api_key(
        self,
        organization_id: int,
        name: str,
        created_by: Optional[int] = None,
        expires_at: Optional[datetime] = None,
    ) -> tuple[APIKeyModel, str]:
        """Create a new API key for an organization.

        Args:
            expires_at: Optional expiry; None = never expires.

        Returns:
            Tuple of (APIKeyModel, raw_api_key)
        """
        # Generate a secure random API key
        raw_api_key, key_hash, key_prefix = generate_api_key()

        async with self.async_session() as session:
            api_key = APIKeyModel(
                organization_id=organization_id,
                name=name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                created_by=created_by,
                is_active=True,
                expires_at=expires_at,
            )
            session.add(api_key)
            await session.commit()
            await session.refresh(api_key)

            return api_key, raw_api_key

    async def get_api_keys_by_organization(
        self, organization_id: int, include_archived: bool = False
    ) -> List[APIKeyModel]:
        """Get all API keys for an organization."""
        async with self.async_session() as session:
            query = select(APIKeyModel).where(
                APIKeyModel.organization_id == organization_id
            )

            if not include_archived:
                query = query.where(APIKeyModel.archived_at.is_(None))

            result = await session.execute(query)
            return result.scalars().all()

    async def get_api_key_by_hash(self, key_hash: str) -> Optional[APIKeyModel]:
        """Get an active, non-archived, non-expired API key by its hash."""
        async with self.async_session() as session:
            result = await session.execute(
                select(APIKeyModel).where(
                    and_(
                        APIKeyModel.key_hash == key_hash,
                        APIKeyModel.is_active == True,
                        APIKeyModel.archived_at.is_(None),
                        or_(
                            APIKeyModel.expires_at.is_(None),
                            APIKeyModel.expires_at > datetime.now(timezone.utc),
                        ),
                    )
                )
            )
            return result.scalars().first()

    async def validate_api_key(self, raw_api_key: str) -> Optional[APIKeyModel]:
        """Validate an API key and return the associated model if valid."""
        key_hash = hash_api_key(raw_api_key)
        api_key = await self.get_api_key_by_hash(key_hash)

        if api_key:
            # Update last_used_at
            from datetime import datetime, timezone

            async with self.async_session() as session:
                await session.execute(
                    APIKeyModel.__table__.update()
                    .where(APIKeyModel.id == api_key.id)
                    .values(last_used_at=datetime.now(timezone.utc))
                )
                await session.commit()

        return api_key

    async def archive_api_key(self, api_key_id: int) -> bool:
        """Archive an API key (soft delete)."""
        from datetime import datetime, timezone

        async with self.async_session() as session:
            result = await session.execute(
                APIKeyModel.__table__.update()
                .where(APIKeyModel.id == api_key_id)
                .values(is_active=False, archived_at=datetime.now(timezone.utc))
            )
            await session.commit()
            return result.rowcount > 0

    async def reactivate_api_key(self, api_key_id: int) -> bool:
        """Reactivate an archived API key."""
        async with self.async_session() as session:
            result = await session.execute(
                APIKeyModel.__table__.update()
                .where(APIKeyModel.id == api_key_id)
                .values(is_active=True, archived_at=None)
            )
            await session.commit()
            return result.rowcount > 0
