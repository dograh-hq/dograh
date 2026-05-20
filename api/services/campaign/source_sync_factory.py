from api.services.campaign.source_sync import CampaignSourceSyncService
from api.services.campaign.sources.csv import CSVSyncService


def get_sync_service(source_type: str) -> CampaignSourceSyncService:
    """Returns appropriate sync service based on source type"""

    services = {
        "csv": CSVSyncService,
    }

    service_class = services.get(source_type)
    if not service_class:
        raise ValueError(f"Unknown source type: {source_type}")

    return service_class()
