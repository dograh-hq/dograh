"""Factory for creating telephony providers.

The factory is now a thin layer over the provider registry. Adding a new
provider requires no changes here — the new provider self-registers when
``api.services.telephony.providers`` is imported.
"""

from typing import Any, Dict, List, Type

from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.telephony import registry
from api.services.telephony.base import TelephonyProvider

_providers_loaded = False


def _ensure_providers_loaded() -> None:
    """Lazy-import the providers package to trigger registration.

    Importing at module load time would create a cycle: provider packages
    import their own ``routes.py``, which imports ``get_telephony_provider``
    from this module — partially-initialized when the providers package
    runs. Deferring until the first factory call breaks the cycle without
    making provider authors think about lazy imports.
    """
    global _providers_loaded
    if not _providers_loaded:
        from api.services.telephony import providers as _  # noqa: F401
        _providers_loaded = True


async def load_telephony_config(organization_id: int) -> Dict[str, Any]:
    """Load telephony configuration from the database for an organization.

    Returns a dict with the provider's normalized config (provider-specific
    keys plus a ``provider`` discriminator). Raises ValueError if no config
    is stored or the provider is unknown.
    """
    _ensure_providers_loaded()
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
    _ensure_providers_loaded()
    return [spec.provider_cls for spec in registry.all_specs()]
