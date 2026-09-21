from datetime import datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.db.base_client import BaseDBClient
from api.db.models import PlatformSettingModel


class PlatformSettingsClient(BaseDBClient):
    """Client for managing global platform settings."""

    async def get_all_settings(self) -> List[PlatformSettingModel]:
        """Fetch all platform settings."""
        async with self.async_session() as session:
            stmt = select(PlatformSettingModel)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_all_settings_dict(self) -> Dict[str, str]:
        """Fetch all platform settings as a key-value dictionary."""
        settings = await self.get_all_settings()
        return {s.key: s.value for s in settings}

    async def get_setting(
        self, key: str, default: Optional[str] = None
    ) -> Optional[str]:
        """Fetch a specific platform setting by key."""
        async with self.async_session() as session:
            stmt = select(PlatformSettingModel).where(PlatformSettingModel.key == key)
            result = await session.execute(stmt)
            setting = result.scalar_one_or_none()
            if setting:
                return setting.value
            return default

    async def set_setting(
        self, key: str, value: str, description: Optional[str] = None
    ) -> PlatformSettingModel:
        """Upsert a single platform setting."""
        async with self.async_session() as session:
            stmt = (
                insert(PlatformSettingModel)
                .values(
                    key=key,
                    value=str(value),
                    description=description,
                    updated_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_update(
                    index_elements=[PlatformSettingModel.key],
                    set_={
                        "value": str(value),
                        "description": (
                            description
                            if description is not None
                            else PlatformSettingModel.description
                        ),
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                .returning(PlatformSettingModel)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.scalar_one()

    async def set_settings_bulk(
        self, settings: Dict[str, str]
    ) -> List[PlatformSettingModel]:
        """Upsert multiple platform settings."""
        results = []
        for k, v in settings.items():
            res = await self.set_setting(key=k, value=str(v))
            results.append(res)
        return results


platform_settings_client = PlatformSettingsClient()
