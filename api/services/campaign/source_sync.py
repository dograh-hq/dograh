from abc import ABC, abstractmethod
from typing import Any, Dict

from loguru import logger


class CampaignSourceSyncService(ABC):
    """Base class for campaign data source synchronization"""

    @abstractmethod
    async def sync_source_data(self, campaign_id: int) -> int:
        """
        Fetches data from source and creates queued_runs
        Each record gets a unique source_uuid based on source type
        Returns: number of records synced
        """
        pass

    @abstractmethod
    async def validate_source_schema(self, source_config: Dict[str, Any]) -> bool:
        """Validates required fields exist in source"""
        pass

    async def get_source_credentials(
        self, organization_id: int, source_type: str
    ) -> Dict[str, Any]:
        """Gets OAuth tokens or API credentials via Nango"""
        # This would be implemented to work with Nango service
        # For now, returning placeholder
        logger.info(
            f"Getting credentials for org {organization_id}, source {source_type}"
        )
        return {}
