"""Factory for creating telephony providers.

Resolves a provider instance from a stored telephony configuration. Three
resolution paths exist:

* by config id — the canonical path used by outbound (test calls, campaigns,
  API triggers) and by the websocket transport once a workflow run has
  ``initial_context.telephony_configuration_id`` stamped on it.
* by org default — used as a fallback when no specific config is requested.
* for inbound — given a detected provider and an account-id from the webhook,
  iterate the org's configs of that provider and return the one whose stored
  account-id credential matches.

Provider classes don't need to know about the new storage shape. They still
receive a normalized config dict containing credentials plus a
``from_numbers`` list of address strings, which the factory assembles by
joining ``telephony_phone_numbers``.
"""

from typing import Any, Dict, List, Optional, Tuple, Type

from loguru import logger

from api.db import db_client
from api.db.models import TelephonyConfigurationModel, WorkflowRunModel
from api.services.telephony import registry
from api.services.telephony.base import TelephonyProvider


async def load_telephony_config_by_id(
    telephony_configuration_id: int,
    organization_id: int,
) -> Dict[str, Any]:
    """Load and normalize the config row by primary key, scoped to the org.

    Returns a dict in the shape each provider class expects in its constructor
    (provider name + provider-specific credentials + ``from_numbers`` list of
    raw address strings). Raises ``ValueError`` if the config doesn't exist
    or doesn't belong to ``organization_id`` — the org scope is what makes
    this safe to expose to user-driven request flows.
    """
    if not telephony_configuration_id:
        raise ValueError("telephony_configuration_id is required")
    if not organization_id:
        raise ValueError("organization_id is required")

    row = await db_client.get_telephony_configuration_for_org(
        telephony_configuration_id, organization_id
    )
    if not row:
        raise ValueError(
            f"Telephony configuration {telephony_configuration_id} not found "
            f"for organization {organization_id}"
        )
    return await _normalize_with_phone_numbers(row)


async def load_default_telephony_config(organization_id: int) -> Dict[str, Any]:
    """Load the org's default outbound config."""
    if not organization_id:
        raise ValueError("organization_id is required")

    row = await db_client.get_default_telephony_configuration(organization_id)
    if not row:
        raise ValueError(
            f"No default telephony configuration found for organization "
            f"{organization_id}"
        )
    return await _normalize_with_phone_numbers(row)


async def find_telephony_config_for_inbound(
    organization_id: int, provider_name: str, account_id: Optional[str]
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Match an inbound webhook to one of the org's configs of the detected
    provider. Returns ``(config_id, normalized_config)`` or None.

    Always scoped to ``organization_id`` — never matches across orgs even if
    two orgs happen to have credentials with the same account_id.
    """
    spec = registry.get_optional(provider_name)
    if not spec:
        return None

    candidates = await db_client.list_telephony_configurations_by_provider(
        organization_id, provider_name
    )
    if not candidates:
        return None

    field = spec.account_id_credential_field
    matched: Optional[TelephonyConfigurationModel] = None

    if not field:
        # Provider has no account-id concept (e.g. ARI); only one config of this
        # provider is meaningful per org.
        if len(candidates) == 1:
            matched = candidates[0]
        else:
            logger.warning(
                f"Provider {provider_name} has multiple configs in org "
                f"{organization_id} but no account_id field to disambiguate; "
                f"picking the default outbound (or first)."
            )
            matched = next(
                (c for c in candidates if c.is_default_outbound), candidates[0]
            )
    elif account_id:
        for cand in candidates:
            stored = (cand.credentials or {}).get(field)
            if stored and stored == account_id:
                matched = cand
                break

    if not matched:
        return None

    normalized = await _normalize_with_phone_numbers(matched)
    return matched.id, normalized


async def get_telephony_provider_by_id(
    telephony_configuration_id: int,
    organization_id: int,
) -> TelephonyProvider:
    config = await load_telephony_config_by_id(
        telephony_configuration_id, organization_id
    )
    return _instantiate(config)


async def get_telephony_provider_for_run(
    workflow_run: WorkflowRunModel,
    organization_id: int,
) -> TelephonyProvider:
    """Resolve the provider for a given workflow run.

    Prefers ``initial_context.telephony_configuration_id`` — stamped at run
    creation by ``/initiate-call``, ``_create_inbound_workflow_run``, the
    campaign dispatcher, and ``public_agent``. Falls back to the org's
    default config so legacy runs created before the multi-config migration
    still resolve.
    """
    cfg_id = (workflow_run.initial_context or {}).get("telephony_configuration_id")
    if cfg_id:
        return await get_telephony_provider_by_id(cfg_id, organization_id)
    return await get_default_telephony_provider(organization_id)


async def get_default_telephony_provider(organization_id: int) -> TelephonyProvider:
    config = await load_default_telephony_config(organization_id)
    return _instantiate(config)


async def get_telephony_provider_for_inbound(
    organization_id: int, provider_name: str, account_id: Optional[str]
) -> Optional[Tuple[int, TelephonyProvider]]:
    """Returns ``(config_id, provider_instance)`` or None when no config matches."""
    match = await find_telephony_config_for_inbound(
        organization_id, provider_name, account_id
    )
    if not match:
        return None
    config_id, config = match
    return config_id, _instantiate(config)


async def load_credentials_for_transport(
    organization_id: int,
    telephony_configuration_id: Optional[int],
    expected_provider: str,
) -> Dict[str, Any]:
    """Helper for per-provider transport modules.

    Resolves the right credentials for a websocket transport given what's
    available on the workflow run. Uses ``telephony_configuration_id`` when
    stamped (the new path), otherwise falls back to the org's default config
    so legacy runs created before the multi-config migration still work.
    Raises ValueError when the resolved config is for a different provider.
    """
    if telephony_configuration_id:
        config = await load_telephony_config_by_id(
            telephony_configuration_id, organization_id
        )
    else:
        config = await load_default_telephony_config(organization_id)

    actual = config.get("provider")
    if actual != expected_provider:
        raise ValueError(
            f"Expected {expected_provider} provider, got {actual} "
            f"(config_id={telephony_configuration_id}, org={organization_id})"
        )
    return config


async def get_all_telephony_providers() -> List[Type[TelephonyProvider]]:
    """All registered provider classes — used by inbound webhook detection."""
    return [spec.provider_cls for spec in registry.all_specs()]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _normalize_with_phone_numbers(
    row: TelephonyConfigurationModel,
) -> Dict[str, Any]:
    """Run the provider's config_loader over the credentials, then attach the
    active phone numbers as a ``from_numbers`` list (raw address strings)."""
    spec = registry.get(row.provider)
    raw = dict(row.credentials or {})
    raw["provider"] = row.provider
    base = spec.config_loader(raw)

    addresses = await db_client.list_active_normalized_addresses_for_config(row.id)
    base["from_numbers"] = addresses
    return base


def _instantiate(config: Dict[str, Any]) -> TelephonyProvider:
    spec = registry.get(config["provider"])
    logger.info(f"Creating {spec.name} telephony provider")
    return spec.provider_cls(config)
