"""Name the tenant on PostHog events.

An organization id is only unique within one deployment (EU org 1 and US org 1
are different tenants sharing one PostHog project), so events never carry it.
They carry the organization's provider id instead, which is the Stack team id
on hosted deployments, and the deployment. The ``account`` group is keyed by
that provider id.
"""

from typing import Any, Optional

from loguru import logger

from api.constants import DOGRAH_DEPLOYMENT_ID
from api.db import db_client
from api.services.posthog_client import POSTHOG_ACCOUNT_GROUP_TYPE, get_posthog

# An organization's provider id never changes, so lookups are cached per
# process. Bounded so a long-lived worker cannot grow it without limit.
_PROVIDER_ID_CACHE_SIZE = 10_000
_provider_ids: dict[int, str] = {}


async def organization_provider_id(organization_id: int) -> Optional[str]:
    """Return the provider id of an organization, cached once found."""
    cached = _provider_ids.get(organization_id)
    if cached is not None:
        return cached
    organization = await db_client.get_organization_by_id(organization_id)
    provider_id = getattr(organization, "provider_id", None)
    if provider_id:
        if len(_provider_ids) >= _PROVIDER_ID_CACHE_SIZE:
            _provider_ids.clear()
        _provider_ids[organization_id] = provider_id
    return provider_id


async def tenant_properties(organization_id: Optional[int]) -> dict[str, Any]:
    """Event properties naming the tenant across deployments.

    Skips the lookup when PostHog is not configured, since nothing is sent.
    Never raises: callers send events after their own writes commit, so a
    failed lookup only leaves the provider id off the event.
    """
    properties: dict[str, Any] = {"deployment_id": DOGRAH_DEPLOYMENT_ID}
    if organization_id is None or get_posthog() is None:
        return properties
    try:
        properties["organization_provider_id"] = await organization_provider_id(
            organization_id
        )
    except Exception:
        logger.exception(
            f"Failed to look up organization {organization_id} for PostHog"
        )
    return properties


def tenant_groups(provider_id: Optional[str]) -> dict[str, str]:
    """``$groups`` for organization lifecycle events, empty without a provider id."""
    if not provider_id:
        return {}
    return {POSTHOG_ACCOUNT_GROUP_TYPE: str(provider_id)}
