"""Factory for creating telephony providers.

Resolves a provider instance from a stored telephony configuration. Three
resolution paths exist:

* by active config id — the canonical path used by outbound (test calls, campaigns,
  API triggers) and by the websocket transport once a workflow run has
  ``initial_context.telephony_configuration_id`` stamped on it.
* by active org default — used as a fallback when no config is requested.
* for inbound — given a detected provider and an account-id from the webhook,
  iterate the org's active configs of that provider and return the one whose
  stored account-id credential matches.

Provider classes don't need to know about the new storage shape. They still
receive a normalized config dict containing credentials plus a
``from_numbers`` list of address strings, which the factory assembles by
joining ``telephony_phone_numbers``.
"""

from typing import Any, Dict, List, Optional, Tuple, Type

from loguru import logger
from pipecat.utils.run_context import set_current_org_id

from api.db import db_client
from api.db.models import TelephonyConfigurationModel, WorkflowRunModel
from api.errors.failure import log_failure
from api.services.telephony import registry
from api.services.telephony.base import SIPConnectivityDetails, TelephonyProvider
from api.services.telephony.failure_reporting import (
    classify_telephony_exception,
    instrument_telephony_provider,
)
from api.services.telephony.registry import (
    ConfigurationSetupState,
    ProviderConnectivity,
    ProviderSetupChecklist,
    caller_id_only_checklist,
)


async def load_telephony_config_by_id(
    telephony_configuration_id: int | str | None,
    organization_id: int,
) -> Dict[str, Any]:
    """Load and normalize the config row by primary key, scoped to the org.

    Returns a dict in the shape each provider class expects in its constructor
    (provider name + provider-specific credentials + ``from_numbers`` list of
    raw address strings). Raises ``ValueError`` if the config doesn't exist,
    is parked, or doesn't belong to ``organization_id`` — the org scope is
    what makes this safe to expose to user-driven request flows.
    """
    try:
        resolved_cfg_id = int(telephony_configuration_id)
    except (TypeError, ValueError) as e:
        raise ValueError("telephony_configuration_id must be an integer") from e
    if not organization_id:
        raise ValueError("organization_id is required")

    row = await db_client.get_telephony_configuration_for_org(
        resolved_cfg_id, organization_id, active_only=True
    )
    if not row:
        raise ValueError(
            f"Telephony configuration {resolved_cfg_id} not found "
            f"for organization {organization_id}"
        )
    if getattr(row, "inactive", False):
        raise ValueError(
            f"Telephony configuration {resolved_cfg_id} is inactive "
            f"for organization {organization_id}"
        )
    return await _normalize_with_phone_numbers(row)


async def load_default_telephony_config(organization_id: int) -> Dict[str, Any]:
    """Load the org's active default outbound config."""
    if not organization_id:
        raise ValueError("organization_id is required")

    row = await db_client.get_default_telephony_configuration(
        organization_id, active_only=True
    )
    if not row:
        raise ValueError(
            f"No default telephony configuration found for organization "
            f"{organization_id}"
        )
    if getattr(row, "inactive", False):
        raise ValueError(
            f"Default telephony configuration is inactive for organization "
            f"{organization_id}"
        )
    return await _normalize_with_phone_numbers(row)


async def find_telephony_config_for_inbound(
    organization_id: int, provider_name: str, account_id: Optional[str]
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Match an inbound webhook to one of the org's configs of the detected
    provider. Returns ``(config_id, normalized_config)`` or None.

    Always scoped to ``organization_id`` and excludes parked rows — never
    matches across orgs even if two orgs have credentials with the same
    account_id.
    """
    spec = registry.get_optional(provider_name)
    if not spec:
        return None

    candidates = await db_client.list_telephony_configurations_by_provider(
        organization_id, provider_name, active_only=True
    )
    # Keep the runtime invariant at the factory boundary as well as in SQL.
    # This protects callers backed by a stale/fake DB client and makes it
    # impossible for normalization to turn a parked row into a live provider.
    candidates = [c for c in candidates if not getattr(c, "inactive", False)]
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
    telephony_configuration_id: int | str | None,
    organization_id: int,
) -> TelephonyProvider:
    set_current_org_id(organization_id)
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
    if cfg_id is not None:
        return await get_telephony_provider_by_id(cfg_id, organization_id)
    return await get_default_telephony_provider(organization_id)


async def get_telephony_provider_for_active_call(
    workflow_run: WorkflowRunModel,
    organization_id: int,
) -> Optional[TelephonyProvider]:
    """Resolve the provider that actually placed this run's call.

    Deliberately *not* ``get_telephony_provider_for_run``. That one falls back to
    the org's current default when a run predates
    ``initial_context.telephony_configuration_id``, which is right for starting
    work but wrong for acting on a leg that is already up: hanging up or
    transferring has to reach the carrier holding the call, and the org default
    may since have changed or be another provider entirely. Sending a Twilio
    call id to WhatsApp does not fail loudly, it just does nothing.

    Returns ``None`` when the owning provider cannot be identified, so callers
    can say so instead of acting on the wrong carrier.
    """
    recorded = (workflow_run.gathered_context or {}).get(
        "provider"
    ) or workflow_run.mode

    cfg_id = (workflow_run.initial_context or {}).get("telephony_configuration_id")
    if cfg_id is not None:
        provider = await get_telephony_provider_by_id(cfg_id, organization_id)
        if not recorded or provider.PROVIDER_NAME == recorded:
            return provider
        logger.warning(
            f"Run {workflow_run.id} records provider {recorded!r} but its "
            f"telephony configuration {cfg_id} is {provider.PROVIDER_NAME!r}; "
            "resolving by the recorded provider instead."
        )

    # Legacy run, or a configuration that has since been repointed. Fall back to
    # the provider the run itself recorded - never to the org default.
    if not recorded or registry.get_optional(recorded) is None:
        # e.g. mode "smallwebrtc": a browser run with no telephony carrier.
        return None

    configs = await db_client.list_telephony_configurations_by_provider(
        organization_id, recorded
    )
    if not configs:
        return None

    if len(configs) > 1:
        # Narrow by the provider's account id, the same credential the inbound
        # matcher uses to tell two configs of one provider apart. Outbound runs
        # record it in gathered_context (WhatsApp stores phone_number_id there
        # via provider_metadata), so the account that placed the call is usually
        # recoverable even for a run with no pinned configuration.
        spec = registry.get_optional(recorded)
        field = spec.account_id_credential_field if spec else ""
        account_id = (workflow_run.gathered_context or {}).get(field) if field else None
        if account_id:
            matched = [
                c for c in configs if (c.credentials or {}).get(field) == account_id
            ]
            if matched:
                configs = matched

    if len(configs) > 1:
        # Picking one here would act on a live call with another account's
        # credentials: the carrier does not know the call id, so the hangup
        # quietly does nothing while reporting that it went somewhere.
        logger.error(
            f"Run {workflow_run.id} has no pinned telephony configuration and "
            f"{len(configs)} {recorded!r} configurations match organization "
            f"{organization_id} (ids={[c.id for c in configs]}); refusing to "
            "guess which one owns the call."
        )
        return None

    return await get_telephony_provider_by_id(configs[0].id, organization_id)


async def get_default_telephony_provider(organization_id: int) -> TelephonyProvider:
    set_current_org_id(organization_id)
    config = await load_default_telephony_config(organization_id)
    return _instantiate(config)


async def get_telephony_provider_for_inbound(
    organization_id: int, provider_name: str, account_id: Optional[str]
) -> Optional[Tuple[int, TelephonyProvider]]:
    """Returns ``(config_id, provider_instance)`` or None when no config matches."""
    set_current_org_id(organization_id)
    match = await find_telephony_config_for_inbound(
        organization_id, provider_name, account_id
    )
    if not match:
        return None
    config_id, config = match
    return config_id, _instantiate(config)


async def load_credentials_for_transport(
    organization_id: int,
    telephony_configuration_id: Optional[int | str],
    expected_provider: str,
) -> Dict[str, Any]:
    """Helper for per-provider transport modules.

    Resolves the right credentials for a websocket transport given what's
    available on the workflow run. Uses ``telephony_configuration_id`` when
    stamped (the new path), otherwise falls back to the org's default config
    so legacy runs created before the multi-config migration still work.
    Raises ValueError when the resolved config is for a different provider.
    """
    resolved_cfg_id = telephony_configuration_id
    if resolved_cfg_id is not None:
        config = await load_telephony_config_by_id(resolved_cfg_id, organization_id)
    else:
        config = await load_default_telephony_config(organization_id)

    actual = config.get("provider")
    if actual != expected_provider:
        raise ValueError(
            f"Expected {expected_provider} provider, got {actual} "
            f"(config_id={resolved_cfg_id}, org={organization_id})"
        )
    return config


async def get_all_telephony_providers() -> List[Type[TelephonyProvider]]:
    """All registered provider classes — used by inbound webhook detection."""
    return [spec.provider_cls for spec in registry.all_specs()]


def get_sip_connectivity_details(
    provider_name: str, credentials: dict[str, Any]
) -> SIPConnectivityDetails | None:
    """Build provider-owned SIP details from stored configuration credentials."""
    spec = registry.get(provider_name)
    if (
        spec.provider_cls.get_sip_connectivity_details
        is TelephonyProvider.get_sip_connectivity_details
    ):
        return None

    raw = dict(credentials)
    raw["provider"] = provider_name
    config = spec.config_loader(raw)
    provider = _instantiate(config)
    return provider.get_sip_connectivity_details()


def get_provider_connectivity(provider_name: str) -> ProviderConnectivity:
    """How a customer gets phone service through this provider.

    Falls back to "api" for an unregistered name so a stale stored provider
    can't break a list response.
    """
    spec = registry.get_optional(provider_name)
    return spec.connectivity if spec else "api"


def get_setup_checklist(
    provider_name: str,
    credentials: dict[str, Any],
    *,
    active_phone_number_count: int,
    inbound_routed_phone_number_count: int,
    enabled_trunk_count: int = 0,
    unassigned_active_phone_number_count: int = 0,
) -> ProviderSetupChecklist | None:
    """Report what setup a stored configuration still needs.

    A provider's own resolver wins. Otherwise every provider that needs a
    caller ID gets the shared one-step checklist, so "credentials are saved"
    is never mistaken for "this can place a call". Returns None only for
    providers that can dial with nothing but credentials.

    Phone-number and trunk counts are passed in rather than queried here so
    list endpoints, which have already loaded them, don't pay per row.
    """
    spec = registry.get_optional(provider_name)
    if spec is None:
        return None

    if spec.setup_checklist_resolver is not None:
        return spec.setup_checklist_resolver(
            credentials,
            ConfigurationSetupState(
                active_phone_number_count=active_phone_number_count,
                inbound_routed_phone_number_count=inbound_routed_phone_number_count,
                enabled_trunk_count=enabled_trunk_count,
                unassigned_active_phone_number_count=(
                    unassigned_active_phone_number_count
                ),
            ),
        )

    if not spec.requires_caller_id:
        return None

    return caller_id_only_checklist(
        display_name=(spec.ui_metadata.display_name if spec.ui_metadata else spec.name),
        has_caller_id=active_phone_number_count > 0,
        docs_url=spec.ui_metadata.docs_url if spec.ui_metadata else None,
    )


async def _normalize_with_phone_numbers(
    row: TelephonyConfigurationModel,
) -> Dict[str, Any]:
    """Run the provider's config_loader over the credentials, then attach the
    active phone numbers as a ``from_numbers`` list (raw address strings) and
    the default caller ID (when one is flagged and active) as
    ``default_from_number``.

    Providers with trunks also get ``trunks`` and ``trunk_id_by_number`` so the
    route a call takes follows from the caller ID it presents, rather than
    being picked from a separate pool.
    """
    spec = registry.get(row.provider)
    raw = dict(row.credentials or {})
    raw["provider"] = row.provider
    base = spec.config_loader(raw)

    addresses = await db_client.list_active_normalized_addresses_for_config(row.id)
    base["from_numbers"] = addresses
    base["telephony_configuration_id"] = row.id
    base["organization_id"] = row.organization_id

    default_row = await db_client.get_default_caller_id(row.id)
    # Membership in the active-address list also guards against a default
    # flag left on a deactivated number.
    if default_row and default_row.address_normalized in addresses:
        base["default_from_number"] = default_row.address_normalized

    if spec.supports_trunks:
        trunks = await db_client.list_trunks_for_config(row.id)
        base["trunks"] = [
            {
                "id": trunk.id,
                "name": trunk.name,
                "enabled": trunk.enabled,
                "settings": dict(trunk.settings or {}),
                "external_id": trunk.external_id,
            }
            for trunk in trunks
        ]
        base[
            "trunk_id_by_number"
        ] = await db_client.get_trunk_ids_by_address_for_config(row.id)
    return base


def _instantiate(config: Dict[str, Any]) -> TelephonyProvider:
    spec = registry.get(config["provider"])
    logger.info(f"Creating {spec.name} telephony provider")
    try:
        provider = spec.provider_cls(config)
    except Exception as exc:
        log_failure(
            classify_telephony_exception(exc, provider=spec.name),
            operation="construct telephony provider",
        )
        raise
    return instrument_telephony_provider(provider)
