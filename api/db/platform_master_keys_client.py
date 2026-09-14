from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import and_, delete, select, update, text

from api.db.base_client import BaseDBClient
from api.db.models import PlatformMasterKeyModel


class PlatformMasterKeysClient(BaseDBClient):
    """Client for managing platform master API keys and pricing."""

    async def get_default_master_key(
        self, service_type: str
    ) -> Optional[PlatformMasterKeyModel]:
        """Fetch the default active master key for a service type (llm, stt, tts)."""
        async with self.async_session() as session:
            stmt = (
                select(PlatformMasterKeyModel)
                .where(
                    and_(
                        PlatformMasterKeyModel.service_type == service_type,
                        PlatformMasterKeyModel.is_default == True,
                        PlatformMasterKeyModel.is_active == True,
                    )
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_master_key_for_provider(
        self, service_type: str, provider: str
    ) -> Optional[PlatformMasterKeyModel]:
        """Fetch active master key for a specific provider."""
        async with self.async_session() as session:
            stmt = (
                select(PlatformMasterKeyModel)
                .where(
                    and_(
                        PlatformMasterKeyModel.service_type == service_type,
                        PlatformMasterKeyModel.provider == provider,
                        PlatformMasterKeyModel.is_active == True,
                    )
                )
                .order_by(PlatformMasterKeyModel.is_default.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_master_keys(
        self, service_type: Optional[str] = None
    ) -> List[PlatformMasterKeyModel]:
        """List all platform master keys, optionally filtered by service_type."""
        async with self.async_session() as session:
            try:
                await session.execute(
                    text("ALTER TABLE platform_master_keys ADD COLUMN IF NOT EXISTS default_model VARCHAR(128)")
                )
                await session.execute(
                    text("ALTER TABLE platform_master_keys ADD COLUMN IF NOT EXISTS default_voice VARCHAR(128)")
                )
                await session.commit()
            except Exception:
                await session.rollback()

            stmt = select(PlatformMasterKeyModel)
            if service_type:
                stmt = stmt.where(PlatformMasterKeyModel.service_type == service_type)
            stmt = stmt.order_by(
                PlatformMasterKeyModel.service_type,
                PlatformMasterKeyModel.is_default.desc(),
                PlatformMasterKeyModel.id,
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_master_key_by_id(
        self, key_id: int
    ) -> Optional[PlatformMasterKeyModel]:
        """Fetch a single master key by ID."""
        async with self.async_session() as session:
            stmt = select(PlatformMasterKeyModel).where(PlatformMasterKeyModel.id == key_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_master_key(
        self,
        service_type: str,
        provider: str,
        api_key: str,
        is_default: bool = False,
        default_model: Optional[str] = None,
        default_voice: Optional[str] = None,
        is_active: bool = True,
        models_pricing: Optional[Dict[str, Any]] = None,
    ) -> PlatformMasterKeyModel:
        """Create a new platform master key."""
        key_prefix = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else api_key[:4] + "..."
        async with self.async_session() as session:
            if is_default:
                # Unset previous default for this service_type
                await session.execute(
                    update(PlatformMasterKeyModel)
                    .where(PlatformMasterKeyModel.service_type == service_type)
                    .values(is_default=False)
                )

            entry = PlatformMasterKeyModel(
                service_type=service_type,
                provider=provider,
                api_key=api_key,
                key_prefix=key_prefix,
                is_default=is_default,
                default_model=default_model,
                default_voice=default_voice,
                is_active=is_active,
                models_pricing=models_pricing or {},
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return entry

    async def update_master_key(
        self,
        key_id: int,
        api_key: Optional[str] = None,
        is_default: Optional[bool] = None,
        default_model: Optional[str] = None,
        default_voice: Optional[str] = None,
        is_active: Optional[bool] = None,
        models_pricing: Optional[Dict[str, Any]] = None,
    ) -> Optional[PlatformMasterKeyModel]:
        """Update an existing master key."""
        async with self.async_session() as session:
            entry = await session.get(PlatformMasterKeyModel, key_id)
            if not entry:
                return None

            if is_default is True:
                await session.execute(
                    update(PlatformMasterKeyModel)
                    .where(PlatformMasterKeyModel.service_type == entry.service_type)
                    .values(is_default=False)
                )
                entry.is_default = True
            elif is_default is False:
                entry.is_default = False

            if default_model is not None:
                entry.default_model = default_model

            if default_voice is not None:
                entry.default_voice = default_voice

            if api_key is not None and api_key.strip():
                entry.api_key = api_key
                entry.key_prefix = (
                    api_key[:8] + "..." + api_key[-4:]
                    if len(api_key) > 12
                    else api_key[:4] + "..."
                )

            if is_active is not None:
                entry.is_active = is_active

            if models_pricing is not None:
                entry.models_pricing = models_pricing

            await session.commit()
            await session.refresh(entry)
            return entry

    async def set_default_model(
        self, key_id: int, default_model: str
    ) -> Optional[PlatformMasterKeyModel]:
        """Set the default model for a platform master key."""
        async with self.async_session() as session:
            entry = await session.get(PlatformMasterKeyModel, key_id)
            if not entry:
                return None
            entry.default_model = default_model
            await session.commit()
            await session.refresh(entry)
            return entry

    async def set_default_voice(
        self, key_id: int, default_voice: str
    ) -> Optional[PlatformMasterKeyModel]:
        """Set the default voice for a platform master key."""
        async with self.async_session() as session:
            entry = await session.get(PlatformMasterKeyModel, key_id)
            if not entry:
                return None
            entry.default_voice = default_voice
            await session.commit()
            await session.refresh(entry)
            return entry

    async def set_default_master_key(
        self, key_id: int
    ) -> Optional[PlatformMasterKeyModel]:
        """Set a master key as the default for its service type."""
        async with self.async_session() as session:
            entry = await session.get(PlatformMasterKeyModel, key_id)
            if not entry:
                return None

            await session.execute(
                update(PlatformMasterKeyModel)
                .where(PlatformMasterKeyModel.service_type == entry.service_type)
                .values(is_default=False)
            )
            entry.is_default = True
            entry.is_active = True
            await session.commit()
            await session.refresh(entry)
            return entry

    async def delete_master_key(self, key_id: int) -> bool:
        """Delete a platform master key."""
        async with self.async_session() as session:
            entry = await session.get(PlatformMasterKeyModel, key_id)
            if not entry:
                return False
            await session.delete(entry)
            await session.commit()
            return True
