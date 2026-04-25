"""Factory for creating telephony providers.

The factory is now a thin layer over the provider registry. Adding a new
provider requires no changes here — the new provider self-registers when
``api.services.telephony.providers`` is imported.
"""

from typing import Any, Dict, List, Type

from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.telephony import (
    providers as _providers,  # noqa: F401  -- triggers registration
)
from api.services.telephony import registry
from api.services.telephony.base import TelephonyProvider


async def load_telephony_config(organization_id: int) -> Dict[str, Any]:
    """Load telephony configuration from the database for an organization.

    Returns a dict with the provider's normalized config (provider-specific
    keys plus a ``provider`` discriminator). Raises ValueError if no config
    is stored or the provider is unknown.
    """
    if not organization_id:
        raise ValueError("Organization ID is required to load telephony configuration")

    logger.debug(f"Loading telephony config from database for org {organization_id}")

    config = await db_client.get_configuration(
        organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
    )

    if not config or not config.value:
        raise ValueError(
            f"No telephony configuration found for organization {organization_id}"
        )

    provider_name = config.value.get("provider", "twilio")
    spec = registry.get(provider_name)
    return spec.config_loader(config.value)


async def get_telephony_provider(organization_id: int) -> TelephonyProvider:
    """Construct the configured telephony provider for an organization."""
    config = await load_telephony_config(organization_id)
    provider_name = config.get("provider", "twilio")
    logger.info(f"Creating {provider_name} telephony provider")
    spec = registry.get(provider_name)
    return spec.provider_cls(config)


async def get_all_telephony_providers() -> List[Type[TelephonyProvider]]:
    """Return all registered telephony provider classes for webhook detection."""
    return [spec.provider_cls for spec in registry.all_specs()]
