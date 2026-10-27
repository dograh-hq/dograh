"""Name the tenant on PostHog events.

Every deployment reports to one PostHog project, and database ids are only
unique within one deployment: EU org 1 and US org 1 are different tenants, as
are their workflow 1, run 1 and user 1. So nothing sent to PostHog carries a
database id. People are keyed by their provider id (the Stack user id), the
tenant by its organization's provider id (the Stack team id on hosted
deployments) plus the deployment, and records by their UUID where they have
one. A record without a UUID goes unnamed rather than misnamed.

The ``account`` group is keyed by the organization's provider id.
"""

from typing import Any, Optional

from loguru import logger

from api.constants import DOGRAH_DEPLOYMENT_ID
from api.db import db_client
from api.services.posthog_client import (
    POSTHOG_ACCOUNT_GROUP_TYPE,
    capture_event,
    get_posthog,
    group_identify,
)

# An organization's provider id never changes, so lookups are cached per
# process. Bounded so a long-lived worker cannot grow it without limit.
_PROVIDER_ID_CACHE_SIZE = 10_000
_provider_ids: dict[int, str] = {}
# Accounts whose group this process has identified, with the name it sent.
_identified_accounts: dict[str, Optional[str]] = {}


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
    """``$groups`` naming the account, empty without a provider id."""
    if not provider_id:
        return {}
    return {POSTHOG_ACCOUNT_GROUP_TYPE: str(provider_id)}


async def capture_tenant_event(
    *,
    distinct_id: Optional[str],
    event: str,
    organization_id: Optional[int],
    properties: Optional[dict[str, Any]] = None,
) -> None:
    """Capture an event about one tenant, in its ``account`` group.

    Identifies the group first, so accounts reached only through API keys or
    local auth have one too. Without a ``distinct_id`` (a call no user
    started) the event is personless but still counts toward the account.

    Not for events that start person-scoped PostHog workflows (signed_up,
    signed_in) or that fan out to every member of an organization: PostHog
    evaluates a ``$groups`` event at both person and group scope, which
    double-triggers those workflows.
    """
    tenant = await tenant_properties(organization_id)
    provider_id = tenant.get("organization_provider_id")
    identify_account(provider_id, distinct_id=distinct_id)
    capture_event(
        distinct_id=distinct_id,
        event=event,
        properties={**(properties or {}), **tenant},
        groups=tenant_groups(provider_id),
    )


def identify_account(
    provider_id: Optional[str],
    *,
    name: Optional[str] = None,
    distinct_id: Optional[str] = None,
) -> None:
    """Create the ``account`` group, or update its name when it changes.

    Sends at most once per process per account and name, for existing
    accounts as well as new ones. One account spans deployments, so the group
    holds only what every deployment agrees on: the provider id and the
    team's name. A call without a name never clears one already sent.
    """
    if not provider_id or get_posthog() is None:
        return
    group_key = str(provider_id)
    if group_key in _identified_accounts and (
        name is None or _identified_accounts[group_key] == name
    ):
        return
    properties: dict[str, Any] = {"organization_provider_id": group_key}
    if name:
        properties["name"] = name
    if not group_identify(
        POSTHOG_ACCOUNT_GROUP_TYPE, group_key, properties, distinct_id=distinct_id
    ):
        return
    if (
        group_key not in _identified_accounts
        and len(_identified_accounts) >= _PROVIDER_ID_CACHE_SIZE
    ):
        _identified_accounts.clear()
    _identified_accounts[group_key] = name or _identified_accounts.get(group_key)
