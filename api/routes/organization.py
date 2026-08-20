from copy import deepcopy
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from api.constants import (
    DEFAULT_CAMPAIGN_RETRY_CONFIG,
    DEFAULT_ORG_CONCURRENCY_LIMIT,
    DEPLOYMENT_MODE,
)
from api.db import db_client
from api.db.models import UserModel
from api.db.telephony_configuration_client import (
    TelephonyConfigurationConflictError,
    TelephonyConfigurationInUseError,
)
from api.db.telephony_phone_number_client import TelephonyPhoneNumberConflictError
from api.db.telephony_trunk_client import TelephonyTrunkConflictError
from api.enums import OrganizationConfigurationKey, PostHogEvent
from api.errors.failure import ErrorSource, classify_exception, log_failure
from api.errors.mps import MPSUnavailableError
from api.schemas.ai_model_configuration import (
    DOGRAH_DEFAULT_LANGUAGE,
    DOGRAH_DEFAULT_VOICE,
    DOGRAH_SPEED_MAX,
    DOGRAH_SPEED_MIN,
    DOGRAH_SPEED_OPTIONS,
    DOGRAH_SPEED_STEP,
    OrganizationAIModelConfigurationResponse,
    OrganizationAIModelConfigurationV2,
)
from api.schemas.organization_preferences import OrganizationPreferences
from api.schemas.telephony_config import (
    TelephonyConfigRequest,
    TelephonyConfigurationCreateRequest,
    TelephonyConfigurationDetail,
    TelephonyConfigurationListItem,
    TelephonyConfigurationListResponse,
    TelephonyConfigurationUpdateRequest,
    TrunkCreateRequest,
    TrunkListResponse,
    TrunkResponse,
    TrunkUpdateRequest,
)
from api.schemas.telephony_phone_number import (
    PhoneNumberCreateRequest,
    PhoneNumberListResponse,
    PhoneNumberResponse,
    PhoneNumberUpdateRequest,
    ProviderSyncStatus,
)
from api.services.auth.depends import (
    get_user,
    get_user_with_selected_organization,
)
from api.services.configuration.ai_model_configuration import (
    check_for_masked_keys_in_ai_model_configuration_v2,
    compile_ai_model_configuration_v2,
    convert_legacy_ai_model_configuration_to_v2,
    get_organization_ai_model_configuration_v2,
    get_resolved_ai_model_configuration,
    mask_ai_model_configuration_v2,
    merge_ai_model_configuration_v2_secrets,
    migrate_workflow_model_configurations_to_v2,
    upsert_organization_ai_model_configuration_v2,
)
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.defaults import DEFAULT_SERVICE_PROVIDERS
from api.services.configuration.masking import is_mask_of, mask_key, mask_user_config
from api.services.configuration.registry import (
    DOGRAH_MULTILINGUAL_AUTODETECT_LANGUAGES,
    DOGRAH_STT_LANGUAGES,
    REGISTRY,
    DograhTTSService,
    ServiceProviders,
    ServiceType,
)
from api.services.mps_billing import ensure_hosted_mps_billing_account_v2
from api.services.mps_service_key_client import mps_service_key_client
from api.services.organization_context import (
    OrganizationContextResponse,
    get_organization_context,
)
from api.services.organization_preferences import (
    external_pbx_integrations_enabled,
    get_organization_preferences,
    upsert_organization_preferences,
)
from api.services.pipecat.tracing_config import normalize_langfuse_host
from api.services.posthog_client import capture_event
from api.services.telephony import registry as telephony_registry
from api.services.telephony.base import ProviderPhoneNumberLookupError
from api.services.telephony.factory import (
    get_provider_connectivity,
    get_setup_checklist,
    get_sip_connectivity_details,
    get_telephony_provider_by_id,
)
from api.services.telephony.inbound_routing import (
    InboundRoutingConflictError,
    assert_no_inbound_routing_conflict,
    canonical_address,
)
from api.services.telephony.registry import ProviderConnectivity, TrunkDesiredState
from api.services.worker_sync.manager import get_worker_sync_manager
from api.services.worker_sync.protocol import WorkerSyncEventType
from api.services.workflow.disposition_codes import (
    END_TASK_REASON_DISPOSITION_CODES,
    SYSTEM_DISPOSITION_CODES,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _sensitive_fields(provider_name: str) -> List[str]:
    """Field names that should be masked when displaying stored config.

    Sourced from ProviderUIField.sensitive in the registry — the same source
    of truth that drives the form-rendering UI.
    """
    spec = telephony_registry.get_optional(provider_name)
    if spec is None or spec.ui_metadata is None:
        return []
    return [f.name for f in spec.ui_metadata.fields if f.sensitive]


def _credentials_for_display(provider_name: str, value: dict) -> dict:
    """Return a copy of ``value`` fit to hand back to a client.

    Sensitive fields are masked, and server-managed bookkeeping fields are
    dropped entirely — clients never send them (provider request schemas do
    not declare them) and they are restored from the stored row on save, so
    echoing them back is noise the UI would only have to hide again.
    """
    out = deepcopy(value)
    for field_name in _sensitive_fields(provider_name):
        v = _get_nested_field(out, field_name)
        if v:
            _set_nested_field(out, field_name, mask_key(str(v)))

    spec = telephony_registry.get_optional(provider_name)
    if spec:
        for field_name in spec.server_managed_credential_fields:
            out.pop(field_name, None)
    return out


class TelephonyProviderUIOption(BaseModel):
    value: str
    label: str


class TelephonyProviderUICondition(BaseModel):
    field: str
    equals: Any


class TelephonyProviderUIField(BaseModel):
    """One form field on a telephony provider's configuration UI."""

    name: str
    label: str
    type: str
    required: bool
    sensitive: bool
    description: Optional[str] = None
    placeholder: Optional[str] = None
    options: Optional[List[TelephonyProviderUIOption]] = None
    visible_when: Optional[TelephonyProviderUICondition] = None
    section: Optional[str] = None


class TelephonyProviderMetadata(BaseModel):
    """UI form metadata for a single telephony provider."""

    provider: str
    display_name: str
    # "api" (buy service from this provider) vs "sip" (bring your own carrier).
    # Lets the UI present the two ways to get calls flowing without naming any
    # provider itself.
    connectivity: ProviderConnectivity = "api"
    fields: List[TelephonyProviderUIField]
    docs_url: Optional[str] = None


class TelephonyProvidersMetadataResponse(BaseModel):
    """List of UI form definitions used by the telephony-config screen."""

    providers: List[TelephonyProviderMetadata]


class TelephonyConfigWarningsResponse(BaseModel):
    """Aggregated telephony-configuration warning counts for the user's org.

    Drives the page banner and nav badge that nudge customers to finish
    optional-but-recommended configuration steps. Shape is a flat dict so
    new warning types can be added without breaking the client.
    """

    telnyx_missing_webhook_public_key_count: int
    vonage_missing_signature_secret_count: int


class ModelConfigurationMetricPrice(BaseModel):
    metric_code: str
    display_name: str
    unit: str
    price_per_minute: float
    currency: str
    rounding_policy: str


class ModelConfigurationPricingResponse(BaseModel):
    """MPS-owned effective prices relevant to model configuration choices."""

    platform_usage: ModelConfigurationMetricPrice | None = None
    dograh_model: ModelConfigurationMetricPrice | None = None


@router.get("/context", response_model=OrganizationContextResponse)
async def get_current_organization_context(user: UserModel = Depends(get_user)):
    """Return organization-scoped configuration signals owned by Dograh."""
    return await get_organization_context(user)


@router.get(
    "/telephony-providers/metadata",
    response_model=TelephonyProvidersMetadataResponse,
)
async def get_telephony_providers_metadata(user: UserModel = Depends(get_user)):
    """Return the list of available telephony providers and their form schemas.

    The UI uses this to render the configuration form generically instead of
    hard-coding fields per provider. Adding a new provider only requires
    declaring its ui_metadata in providers/<name>/__init__.py.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    external_pbx_enabled = await external_pbx_integrations_enabled(
        user.selected_organization_id
    )
    providers = []
    for spec in telephony_registry.all_specs():
        if spec.ui_metadata is None:
            continue
        providers.append(
            TelephonyProviderMetadata(
                provider=spec.name,
                display_name=spec.ui_metadata.display_name,
                connectivity=spec.connectivity,
                fields=[
                    TelephonyProviderUIField(
                        name=f.name,
                        label=f.label,
                        type=f.type,
                        required=f.required,
                        sensitive=f.sensitive,
                        description=f.description,
                        placeholder=f.placeholder,
                        options=(
                            [
                                {"value": option.value, "label": option.label}
                                for option in f.options
                            ]
                            if f.options
                            else None
                        ),
                        visible_when=(
                            {
                                "field": f.visible_when.field,
                                "equals": f.visible_when.equals,
                            }
                            if f.visible_when
                            else None
                        ),
                        section=f.section,
                    )
                    for f in spec.ui_metadata.fields
                    if not f.feature_gate
                    or (
                        f.feature_gate == "external_pbx_integrations"
                        and external_pbx_enabled
                    )
                ],
                docs_url=spec.ui_metadata.docs_url,
            )
        )
    return TelephonyProvidersMetadataResponse(providers=providers)


@router.get(
    "/telephony-config-warnings",
    response_model=TelephonyConfigWarningsResponse,
)
async def get_telephony_config_warnings(user: UserModel = Depends(get_user)):
    """Return aggregated warning counts for the current org's telephony configs.

    Surfaces provider configs missing webhook-verification credentials.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    telnyx_missing = await db_client.count_telnyx_configs_missing_webhook_public_key(
        user.selected_organization_id
    )
    vonage_missing = await db_client.count_vonage_configs_missing_signature_secret(
        user.selected_organization_id
    )
    return TelephonyConfigWarningsResponse(
        telnyx_missing_webhook_public_key_count=telnyx_missing,
        vonage_missing_signature_secret_count=vonage_missing,
    )


# ---------------------------------------------------------------------------
# AI model configurations v2
# ---------------------------------------------------------------------------


def _dograh_allows_custom_voice() -> bool:
    extra = DograhTTSService.model_fields["voice"].json_schema_extra
    if isinstance(extra, dict):
        return bool(extra.get("allow_custom_input", False))
    return False


def _byok_provider_schemas(service_type: ServiceType) -> dict[str, dict]:
    return {
        provider: model_cls.model_json_schema()
        for provider, model_cls in REGISTRY[service_type].items()
        if provider != ServiceProviders.DOGRAH.value
    }


async def _model_configuration_v2_response(
    *,
    user: UserModel,
    configuration: OrganizationAIModelConfigurationV2 | None = None,
) -> OrganizationAIModelConfigurationResponse:
    resolved = await get_resolved_ai_model_configuration(
        organization_id=user.selected_organization_id,
    )
    raw_configuration = (
        configuration
        if configuration is not None
        else resolved.organization_configuration
    )
    return OrganizationAIModelConfigurationResponse(
        configuration=mask_ai_model_configuration_v2(raw_configuration),
        effective_configuration=mask_user_config(resolved.effective),
        source=resolved.source,
    )


@router.get("/model-configurations/v2/defaults")
async def get_model_configuration_v2_defaults(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    byok_default_providers = {
        service: provider
        for service, provider in DEFAULT_SERVICE_PROVIDERS.items()
        if provider != ServiceProviders.DOGRAH.value
    }
    return {
        "dograh": {
            "voices": [DOGRAH_DEFAULT_VOICE],
            "allow_custom_input": _dograh_allows_custom_voice(),
            "speeds": list(DOGRAH_SPEED_OPTIONS),
            "speed_range": {
                "min": DOGRAH_SPEED_MIN,
                "max": DOGRAH_SPEED_MAX,
                "step": DOGRAH_SPEED_STEP,
            },
            "languages": DOGRAH_STT_LANGUAGES,
            "multilingual_languages": DOGRAH_MULTILINGUAL_AUTODETECT_LANGUAGES,
            "defaults": {
                "voice": DOGRAH_DEFAULT_VOICE,
                "speed": 1.0,
                "language": DOGRAH_DEFAULT_LANGUAGE,
            },
        },
        "byok": {
            "pipeline": {
                "llm": _byok_provider_schemas(ServiceType.LLM),
                "tts": _byok_provider_schemas(ServiceType.TTS),
                "stt": _byok_provider_schemas(ServiceType.STT),
                "embeddings": _byok_provider_schemas(ServiceType.EMBEDDINGS),
                "default_providers": byok_default_providers,
            },
            "realtime": {
                "realtime": _byok_provider_schemas(ServiceType.REALTIME),
                "llm": _byok_provider_schemas(ServiceType.LLM),
                "embeddings": _byok_provider_schemas(ServiceType.EMBEDDINGS),
                "default_providers": byok_default_providers,
            },
        },
    }


@router.get(
    "/model-configurations/v2",
    response_model=OrganizationAIModelConfigurationResponse,
)
async def get_model_configuration_v2(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    return await _model_configuration_v2_response(user=user)


@router.get(
    "/model-configurations/v2/pricing",
    response_model=ModelConfigurationPricingResponse,
)
async def get_model_configuration_pricing(
    user: UserModel = Depends(get_user_with_selected_organization),
) -> ModelConfigurationPricingResponse:
    """Return the hosted organization prices shown in Model Configurations."""
    if DEPLOYMENT_MODE == "oss":
        return ModelConfigurationPricingResponse()

    try:
        pricing = await mps_service_key_client.get_billing_pricing(
            user.selected_organization_id,
        )
        return ModelConfigurationPricingResponse.model_validate(pricing)
    except MPSUnavailableError:
        # The MPS boundary emitted the classified failure. The app-level handler
        # converts this typed dependency failure to a customer-safe HTTP 503.
        raise
    except Exception as exc:
        log_failure(
            classify_exception(
                exc,
                source=ErrorSource.PLATFORM,
                provider="dograh",
                error_owner="operator",
            ),
            organization_id=user.selected_organization_id,
            operation="validate_billing_pricing_response",
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve model configuration pricing",
        ) from exc


@router.put(
    "/model-configurations/v2",
    response_model=OrganizationAIModelConfigurationResponse,
)
async def save_model_configuration_v2(
    request: OrganizationAIModelConfigurationV2,
    user: UserModel = Depends(get_user_with_selected_organization),
):
    organization_id = user.selected_organization_id
    existing = await get_organization_ai_model_configuration_v2(organization_id)
    configuration = merge_ai_model_configuration_v2_secrets(request, existing)
    try:
        check_for_masked_keys_in_ai_model_configuration_v2(configuration)
        effective = compile_ai_model_configuration_v2(configuration)
        await UserConfigurationValidator().validate(
            effective,
            organization_id=organization_id,
            created_by=user.provider_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=exc.args[0])

    await upsert_organization_ai_model_configuration_v2(
        organization_id,
        configuration,
    )
    return await _model_configuration_v2_response(
        user=user,
        configuration=configuration,
    )


@router.get("/model-configurations/v2/migration-preview")
async def preview_model_configuration_v2_migration(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    legacy = await db_client.get_user_configurations(user.id)
    try:
        configuration = convert_legacy_ai_model_configuration_to_v2(legacy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "configuration": mask_ai_model_configuration_v2(configuration),
        "effective_configuration": mask_user_config(
            compile_ai_model_configuration_v2(configuration)
        ),
    }


@router.post(
    "/model-configurations/v2/migrate",
    response_model=OrganizationAIModelConfigurationResponse,
)
async def migrate_model_configuration_v2(
    force: bool = Query(default=False),
    user: UserModel = Depends(get_user_with_selected_organization),
):
    organization_id = user.selected_organization_id
    existing = await get_organization_ai_model_configuration_v2(organization_id)
    if existing is not None and not force:
        raise HTTPException(
            status_code=409,
            detail="Organization already has a v2 model configuration",
        )

    legacy = await db_client.get_user_configurations(user.id)
    try:
        configuration = convert_legacy_ai_model_configuration_to_v2(legacy)
        effective = compile_ai_model_configuration_v2(configuration)
        await UserConfigurationValidator().validate(
            effective,
            organization_id=organization_id,
            created_by=user.provider_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=exc.args[0])

    if DEPLOYMENT_MODE != "oss":
        try:
            await ensure_hosted_mps_billing_account_v2(
                organization_id,
                created_by=str(user.provider_id),
            )
        except Exception as exc:
            logger.error(
                "Failed to initialize MPS billing account for organization {}: {}",
                organization_id,
                exc,
            )
            raise HTTPException(
                status_code=502,
                detail="Failed to initialize MPS billing account",
            )

    await upsert_organization_ai_model_configuration_v2(
        organization_id,
        configuration,
    )
    await migrate_workflow_model_configurations_to_v2(
        organization_id=organization_id,
        fallback_user_config=legacy,
    )
    return await _model_configuration_v2_response(
        user=user,
        configuration=configuration,
    )


class DispositionCodesResponse(BaseModel):
    """Disposition codes selectable in org-wide run filters."""

    codes: List[str] = Field(
        description=(
            "Every code that can appear in `gathered_context."
            "mapped_call_disposition`: the platform's built-in dispositions "
            "plus any custom mapped codes this organization's runs have "
            "produced."
        )
    )
    end_task_reason_codes: List[str] = Field(
        description="Disposition codes defined by Pipecat's EndTaskReason enum."
    )


@router.get("/disposition-codes", response_model=DispositionCodesResponse)
async def get_disposition_codes(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    """Serve the disposition catalog so clients never hardcode the list.

    Built-in codes come from the enums that write the field; custom codes are
    whatever this organization's finished runs have actually recorded, so an
    org with a disposition mapping still gets a usable filter.
    """
    custom_codes = await db_client.get_organization_disposition_codes(
        user.selected_organization_id
    )
    known = set(SYSTEM_DISPOSITION_CODES)
    return DispositionCodesResponse(
        codes=[
            *SYSTEM_DISPOSITION_CODES,
            *sorted(code for code in custom_codes if code not in known),
        ],
        end_task_reason_codes=list(END_TASK_REASON_DISPOSITION_CODES),
    )


@router.get("/preferences", response_model=OrganizationPreferences)
async def get_preferences(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    organization_id = user.selected_organization_id
    return await get_organization_preferences(organization_id)


@router.put("/preferences", response_model=OrganizationPreferences)
async def save_preferences(
    request: OrganizationPreferences,
    user: UserModel = Depends(get_user_with_selected_organization),
):
    organization_id = user.selected_organization_id
    return await upsert_organization_preferences(
        organization_id,
        request,
    )


@router.get(
    "/model-configurations/preferences",
    response_model=OrganizationPreferences,
    include_in_schema=False,
)
async def get_model_configuration_preferences_legacy(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    return await get_preferences(user=user)


def preserve_masked_fields(provider: str, request_dict: dict, existing: dict):
    """If the client re-submitted a masked sensitive field, restore the original."""
    for field_name in _sensitive_fields(provider):
        v = _get_nested_field(request_dict, field_name)
        existing_value = _get_nested_field(existing, field_name)
        if v and is_mask_of(v, existing_value or ""):
            _set_nested_field(request_dict, field_name, existing_value)


def _get_nested_field(value: dict, dotted_path: str):
    current = value
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_nested_field(value: dict, dotted_path: str, field_value) -> None:
    current = value
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = field_value


def _credentials_from_payload(config: TelephonyConfigRequest) -> dict:
    """Provider credentials only — strip provider/from_numbers from the payload."""
    payload = config.model_dump()
    payload.pop("provider", None)
    payload.pop("from_numbers", None)
    return payload


async def _run_preprocess_hook(
    provider: str,
    credentials: dict,
    existing_credentials: dict | None = None,
) -> dict:
    """Preserve same-account server fields, then preprocess credentials."""
    spec = telephony_registry.get_optional(provider)
    if not spec:
        return credentials

    credentials = dict(credentials)
    account_field = spec.account_id_credential_field
    account_changed = bool(
        existing_credentials is not None
        and account_field
        and credentials.get(account_field) != existing_credentials.get(account_field)
    )
    invalidated_fields = (
        set(spec.account_scoped_server_managed_credential_fields)
        if account_changed
        else set()
    )
    for field in spec.server_managed_credential_fields:
        credentials.pop(field, None)
        if (
            field not in invalidated_fields
            and existing_credentials is not None
            and field in existing_credentials
        ):
            credentials[field] = existing_credentials[field]

    if spec.preprocess_credentials_on_save:
        return await spec.preprocess_credentials_on_save(
            credentials, existing_credentials
        )
    return credentials


def _phone_number_to_response(
    row, inbound_workflow_name: Optional[str] = None
) -> PhoneNumberResponse:
    response = PhoneNumberResponse.model_validate(row)
    response.inbound_workflow_name = inbound_workflow_name
    return response


async def _ensure_provider_phone_number(
    config_id: int,
    organization_id: int,
    address: str,
    country_hint: str | None = None,
) -> None:
    """Provision an address when supported, otherwise confirm ownership.

    Provider provisioning is opt-in and idempotent. Other providers continue
    through their read-only inventory lookup; PBX-managed providers explicitly
    opt out through ``validate_phone_number``.
    """
    provider = None
    try:
        canonical_address = normalize_telephony_address(
            address, country_hint=country_hint
        ).canonical
        provider = await get_telephony_provider_by_id(config_id, organization_id)
        provision_phone_number = getattr(provider, "provision_phone_number", None)
        if callable(provision_phone_number):
            provisioned = await provision_phone_number(canonical_address)
            if provisioned is not None:
                if not provisioned.ok:
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            provisioned.message
                            or "Provider rejected phone-number provisioning"
                        ),
                    )
                return
        result = await provider.validate_phone_number(canonical_address)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ProviderPhoneNumberLookupError as e:
        # The lookup runs against the org's own provider account, so whatever
        # failed — their credentials or their provider — routes to the user,
        # who also receives the detail in the HTTP response below.
        log_failure(
            classify_exception(
                e.__cause__ or e,
                source=ErrorSource.TELEPHONY,
                provider=getattr(provider, "PROVIDER_NAME", None),
                error_owner="user",
            ),
            organization_id=organization_id,
            telephony_configuration_id=config_id,
            address=address,
        )
        raise HTTPException(status_code=502, detail=str(e)) from e

    if not result.ok:
        raise HTTPException(
            status_code=400,
            detail=result.message
            or "Phone number is not owned by this provider account",
        )


async def _sync_inbound_for_phone_number(
    config_id: int, organization_id: int, address: str, *, attach: bool = True
) -> ProviderSyncStatus:
    """Push inbound webhook configuration to the provider.

    ``attach=True``: ask the provider to route this number's inbound calls
    to our workflow-agnostic dispatcher (``/api/v1/telephony/inbound/run``).
    ``attach=False``: ask the provider to detach. The dispatcher resolves
    the workflow from the called number's ``inbound_workflow_id``, so the
    webhook URL is the same for every assignment — providers only need to
    bind/unbind the number, not rewrite per-workflow URLs.
    """
    try:
        provider = await get_telephony_provider_by_id(config_id, organization_id)
    except Exception as e:
        logger.error(f"Failed to load telephony provider for config {config_id}: {e}")
        return ProviderSyncStatus(ok=False, message=f"Provider load failed: {e}")

    webhook_url = None
    if attach:
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = f"{backend_endpoint}/api/v1/telephony/inbound/run"

    try:
        result = await provider.configure_inbound(address, webhook_url)
    except Exception as e:
        logger.error(
            f"Provider configure_inbound raised for config {config_id} "
            f"address {address}: {e}"
        )
        return ProviderSyncStatus(ok=False, message=f"Provider sync failed: {e}")

    return ProviderSyncStatus(ok=result.ok, message=result.message)


# ---------------------------------------------------------------------------
# Multi-config CRUD
# ---------------------------------------------------------------------------


@router.get("/telephony-configs", response_model=TelephonyConfigurationListResponse)
async def list_telephony_configurations(user: UserModel = Depends(get_user)):
    """List the org's telephony configurations with phone-number counts."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    rows = await db_client.list_telephony_configurations(user.selected_organization_id)
    items: List[TelephonyConfigurationListItem] = []
    for row in rows:
        numbers = await db_client.list_phone_numbers_for_config(row.id)
        active = [n for n in numbers if n.is_active]
        trunks = await _list_trunks_if_supported(row.provider, row.id)
        checklist = get_setup_checklist(
            row.provider,
            row.credentials or {},
            active_phone_number_count=len(active),
            inbound_routed_phone_number_count=len(
                [n for n in active if n.inbound_workflow_id]
            ),
            enabled_trunk_count=len([t for t in trunks if t.enabled]),
            unassigned_active_phone_number_count=len(
                [n for n in active if n.telephony_trunk_id is None]
            ),
        )
        items.append(
            TelephonyConfigurationListItem(
                id=row.id,
                name=row.name,
                provider=row.provider,
                connectivity=get_provider_connectivity(row.provider),
                is_default_outbound=row.is_default_outbound,
                inactive=row.inactive,
                inactive_since=row.inactive_since,
                inactive_reason=row.inactive_reason,
                phone_number_count=len(active),
                is_ready_for_outbound=(
                    checklist.ready_for_outbound if checklist else True
                ),
                outbound_blocked_reason=(
                    checklist.outbound_blocked_reason if checklist else None
                ),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return TelephonyConfigurationListResponse(configurations=items)


@router.post("/telephony-configs", response_model=TelephonyConfigurationDetail)
async def create_telephony_configuration(
    request: TelephonyConfigurationCreateRequest,
    user: UserModel = Depends(get_user),
):
    """Create a new telephony configuration for the org."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    credentials = _credentials_from_payload(request.config)
    credentials = await _run_preprocess_hook(request.config.provider, credentials)

    try:
        row = await db_client.create_telephony_configuration(
            organization_id=user.selected_organization_id,
            name=request.name,
            provider=request.config.provider,
            credentials=credentials,
            is_default_outbound=request.is_default_outbound,
        )
    except TelephonyConfigurationConflictError as e:
        if "uq_telephony_configurations_org_name" in str(e):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A telephony configuration named '{request.name}' already "
                    f"exists in this organization. Pick a different name."
                ),
            )
        raise HTTPException(
            status_code=409,
            detail="Telephony configuration violates a uniqueness constraint.",
        )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.TELEPHONY_CONFIGURED,
        properties={
            "provider": request.config.provider,
            "organization_id": user.selected_organization_id,
            "config_id": row.id,
        },
    )

    return await _detail_response(row)


@router.get(
    "/telephony-configs/{config_id}", response_model=TelephonyConfigurationDetail
)
async def get_telephony_configuration_by_id(
    config_id: int, user: UserModel = Depends(get_user)
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    row = await db_client.get_telephony_configuration_for_org(
        config_id, user.selected_organization_id, active_only=False
    )
    if not row:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")
    return await _detail_response(row)


@router.put(
    "/telephony-configs/{config_id}", response_model=TelephonyConfigurationDetail
)
async def update_telephony_configuration(
    config_id: int,
    request: TelephonyConfigurationUpdateRequest,
    user: UserModel = Depends(get_user),
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    existing = await db_client.get_telephony_configuration_for_org(
        config_id, user.selected_organization_id, active_only=False
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")

    credentials = None
    if request.config is not None:
        if request.config.provider != existing.provider:
            raise HTTPException(
                status_code=400,
                detail="Provider cannot be changed; create a new configuration instead.",
            )
        credentials = _credentials_from_payload(request.config)
        preserve_masked_fields(
            existing.provider, credentials, existing.credentials or {}
        )
        credentials = await _run_preprocess_hook(
            existing.provider,
            credentials,
            existing.credentials or {},
        )

        # The account id is one component of the routing key of every phone
        # number on this configuration, so changing it moves all of them at
        # once — hence the check spans the whole set rather than one address.
        # It no-ops when the account id is unchanged, so a rename or a secret
        # rotation is never blocked by a collision already present in the data.
        existing_numbers = await db_client.list_phone_numbers_for_config(config_id)
        try:
            await assert_no_inbound_routing_conflict(
                provider=existing.provider,
                credentials=credentials,
                addresses=[n.address_normalized for n in existing_numbers],
                organization_id=user.selected_organization_id,
                previous_credentials=existing.credentials or {},
                exclude_configuration_id=config_id,
            )
        except InboundRoutingConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))

    row = await db_client.update_telephony_configuration(
        config_id=config_id,
        organization_id=user.selected_organization_id,
        name=request.name,
        credentials=credentials,
    )

    return await _detail_response(row)


@router.post(
    "/telephony-configs/{config_id}/set-default-outbound",
    response_model=TelephonyConfigurationDetail,
)
async def set_default_outbound(config_id: int, user: UserModel = Depends(get_user)):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    row = await db_client.set_default_telephony_configuration(
        config_id, user.selected_organization_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")
    return await _detail_response(row)


@router.post(
    "/telephony-configs/{config_id}/reactivate",
    response_model=TelephonyConfigurationDetail,
)
async def reactivate_telephony_configuration(
    config_id: int, user: UserModel = Depends(get_user)
):
    """Clear the inactive flag so connection workers pick the config up again.

    A config is deactivated automatically when it keeps failing to connect, and
    workers never re-enable it on their own. This endpoint is the only way back,
    so the customer fixes their PBX and then explicitly retries.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    updated = await db_client.set_telephony_configuration_active(
        config_id, user.selected_organization_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")

    row = await db_client.get_telephony_configuration_for_org(
        config_id, user.selected_organization_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")
    return await _detail_response(row)


@router.delete("/telephony-configs/{config_id}")
async def delete_telephony_configuration(
    config_id: int, user: UserModel = Depends(get_user)
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    try:
        deleted = await db_client.delete_telephony_configuration(
            config_id, user.selected_organization_id
        )
    except TelephonyConfigurationInUseError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")
    return {"message": "Telephony configuration deleted"}


async def _detail_response(row) -> TelephonyConfigurationDetail:
    masked = _credentials_for_display(row.provider, row.credentials or {})
    numbers = await db_client.list_phone_numbers_for_config(row.id)
    active = [n for n in numbers if n.is_active]
    trunks = await _list_trunks_if_supported(row.provider, row.id)
    numbers_per_trunk: dict[int, int] = {}
    for number in numbers:
        if number.telephony_trunk_id is not None:
            numbers_per_trunk[number.telephony_trunk_id] = (
                numbers_per_trunk.get(number.telephony_trunk_id, 0) + 1
            )
    return TelephonyConfigurationDetail(
        id=row.id,
        name=row.name,
        provider=row.provider,
        connectivity=get_provider_connectivity(row.provider),
        is_default_outbound=row.is_default_outbound,
        inactive=row.inactive,
        inactive_since=row.inactive_since,
        inactive_reason=row.inactive_reason,
        credentials=masked,
        sip_connectivity=get_sip_connectivity_details(
            row.provider, row.credentials or {}
        ),
        # Built from the stored (unmasked) credentials: the checklist reports
        # whether a field is set, never what it is.
        setup_checklist=get_setup_checklist(
            row.provider,
            row.credentials or {},
            active_phone_number_count=len(active),
            inbound_routed_phone_number_count=len(
                [n for n in active if n.inbound_workflow_id]
            ),
            enabled_trunk_count=len([t for t in trunks if t.enabled]),
            unassigned_active_phone_number_count=len(
                [n for n in active if n.telephony_trunk_id is None]
            ),
        ),
        supports_trunks=_provider_supports_trunks(row.provider),
        trunks=[
            _trunk_to_response(trunk, numbers_per_trunk.get(trunk.id, 0))
            for trunk in trunks
        ],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Trunks (nested under a config)
# ---------------------------------------------------------------------------


def _provider_supports_trunks(provider: str) -> bool:
    spec = telephony_registry.get_optional(provider)
    return bool(spec and spec.supports_trunks)


async def _list_trunks_if_supported(provider: str, config_id: int):
    """Trunk rows for a provider that has them, and nothing for one that
    doesn't — so a Twilio detail response never pays for the query."""
    if not _provider_supports_trunks(provider):
        return []
    return await db_client.list_trunks_for_config(config_id)


def _trunk_to_response(trunk, phone_number_count: int = 0) -> TrunkResponse:
    return TrunkResponse(
        id=trunk.id,
        name=trunk.name,
        enabled=trunk.enabled,
        settings=dict(trunk.settings or {}),
        phone_number_count=phone_number_count,
        created_at=trunk.created_at,
        updated_at=trunk.updated_at,
    )


def _require_trunk_support(provider: str):
    spec = telephony_registry.get_optional(provider)
    if not spec or not spec.supports_trunks:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Dograh does not model trunks on {provider} configurations — "
                f"calls route through the provider account itself. Numbers on "
                f"this configuration need no trunk."
            ),
        )
    return spec


def _validated_trunk_settings(spec, settings: dict) -> dict:
    """Run the provider's trunk schema over the operator-supplied settings."""
    try:
        return spec.trunk_settings_cls(**(settings or {})).model_dump()
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e


async def _reject_duplicate_trunk_name(
    config_id: int, name: str, *, exclude_trunk_id: int | None = None
) -> None:
    """Name collisions are reported before anything is provisioned remotely,
    so a rejected save leaves no trunk behind on the provider's side."""
    for existing in await db_client.list_trunks_for_config(config_id):
        if existing.id != exclude_trunk_id and existing.name == name:
            raise HTTPException(
                status_code=409,
                detail=f"A trunk named '{name}' already exists on this configuration.",
            )


@router.get("/telephony-configs/{config_id}/trunks", response_model=TrunkListResponse)
async def list_telephony_trunks(config_id: int, user: UserModel = Depends(get_user)):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    cfg = await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)
    trunks = await _list_trunks_if_supported(cfg.provider, config_id)
    numbers = await db_client.list_phone_numbers_for_config(config_id)
    counts: dict[int, int] = {}
    for number in numbers:
        if number.telephony_trunk_id is not None:
            counts[number.telephony_trunk_id] = (
                counts.get(number.telephony_trunk_id, 0) + 1
            )
    return TrunkListResponse(
        trunks=[_trunk_to_response(t, counts.get(t.id, 0)) for t in trunks]
    )


@router.post("/telephony-configs/{config_id}/trunks", response_model=TrunkResponse)
async def create_telephony_trunk(
    config_id: int,
    request: TrunkCreateRequest,
    user: UserModel = Depends(get_user),
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    cfg = await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)
    spec = _require_trunk_support(cfg.provider)
    settings = _validated_trunk_settings(spec, request.settings)
    name = request.name.strip()
    await _reject_duplicate_trunk_name(config_id, name)

    # Provision remotely first: a provider that refuses the trunk should leave
    # no row claiming it exists.
    external_id = None
    if spec.apply_trunk_on_save:
        external_id = await spec.apply_trunk_on_save(
            cfg.credentials or {},
            TrunkDesiredState(name=name, enabled=request.enabled, settings=settings),
        )

    try:
        trunk = await db_client.create_trunk(
            telephony_configuration_id=config_id,
            name=name,
            enabled=request.enabled,
            settings=settings,
            external_id=external_id,
        )
    except TelephonyTrunkConflictError:
        raise HTTPException(
            status_code=409,
            detail=f"A trunk named '{name}' already exists on this configuration.",
        )
    return _trunk_to_response(trunk)


@router.put(
    "/telephony-configs/{config_id}/trunks/{trunk_id}", response_model=TrunkResponse
)
async def update_telephony_trunk(
    config_id: int,
    trunk_id: int,
    request: TrunkUpdateRequest,
    user: UserModel = Depends(get_user),
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    cfg = await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)
    spec = _require_trunk_support(cfg.provider)
    existing = await db_client.get_trunk_for_config(trunk_id, config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Trunk not found")

    name = request.name.strip() if request.name is not None else existing.name
    enabled = request.enabled if request.enabled is not None else existing.enabled
    settings = (
        _validated_trunk_settings(spec, request.settings)
        if request.settings is not None
        else dict(existing.settings or {})
    )
    if name != existing.name:
        await _reject_duplicate_trunk_name(config_id, name, exclude_trunk_id=trunk_id)

    external_id = existing.external_id
    if spec.apply_trunk_on_save:
        external_id = await spec.apply_trunk_on_save(
            cfg.credentials or {},
            TrunkDesiredState(
                name=name,
                enabled=enabled,
                settings=settings,
                external_id=existing.external_id,
            ),
        )

    try:
        trunk = await db_client.update_trunk(
            trunk_id=trunk_id,
            telephony_configuration_id=config_id,
            name=name,
            enabled=enabled,
            settings=settings,
            # Empty string clears; None would mean "leave alone", which would
            # strand the row if the provider stopped reporting an id.
            external_id=external_id or "",
        )
    except TelephonyTrunkConflictError:
        raise HTTPException(
            status_code=409,
            detail=f"A trunk named '{name}' already exists on this configuration.",
        )
    if not trunk:
        raise HTTPException(status_code=404, detail="Trunk not found")
    return _trunk_to_response(trunk)


@router.delete("/telephony-configs/{config_id}/trunks/{trunk_id}")
async def delete_telephony_trunk(
    config_id: int, trunk_id: int, user: UserModel = Depends(get_user)
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    cfg = await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)
    spec = _require_trunk_support(cfg.provider)
    existing = await db_client.get_trunk_for_config(trunk_id, config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Trunk not found")

    # The FK would quietly null these out. Silently unassigning a number's
    # carrier is the mismatch this model exists to prevent, so make it a
    # decision the operator has to take.
    attached = await db_client.count_phone_numbers_for_trunk(trunk_id)
    if attached:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{attached} phone number(s) dial out over '{existing.name}'. "
                f"Move them to another trunk before deleting it."
            ),
        )

    if spec.remove_trunk_on_delete:
        await spec.remove_trunk_on_delete(
            cfg.credentials or {},
            TrunkDesiredState(
                name=existing.name,
                enabled=existing.enabled,
                settings=dict(existing.settings or {}),
                external_id=existing.external_id,
            ),
        )

    await db_client.delete_trunk(trunk_id, config_id)
    return {"success": True}


# ---------------------------------------------------------------------------
# Phone numbers (nested under a config)
# ---------------------------------------------------------------------------


async def _ensure_config_belongs_to_org(config_id: int, organization_id: int):
    cfg = await db_client.get_telephony_configuration_for_org(
        config_id, organization_id, active_only=False
    )
    if not cfg:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")
    return cfg


async def _ensure_trunk_belongs_to_config(trunk_id: int, config_id: int):
    """A number can only be authorised on a trunk of its own configuration —
    the FK alone would happily accept another organization's trunk id."""
    trunk = await db_client.get_trunk_for_config(trunk_id, config_id)
    if not trunk:
        raise HTTPException(
            status_code=404, detail="Trunk not found on this telephony configuration"
        )
    return trunk


async def _ensure_workflow_belongs_to_org(workflow_id: int, organization_id: int):
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.get(
    "/telephony-configs/{config_id}/phone-numbers",
    response_model=PhoneNumberListResponse,
)
async def list_phone_numbers(config_id: int, user: UserModel = Depends(get_user)):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)

    rows = await db_client.list_phone_numbers_with_workflow_name_for_config(config_id)
    return PhoneNumberListResponse(
        phone_numbers=[_phone_number_to_response(r, name) for r, name in rows]
    )


@router.post(
    "/telephony-configs/{config_id}/phone-numbers",
    response_model=PhoneNumberResponse,
)
async def create_phone_number(
    config_id: int,
    request: PhoneNumberCreateRequest,
    user: UserModel = Depends(get_user),
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    cfg = await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)

    if request.inbound_workflow_id is not None:
        await _ensure_workflow_belongs_to_org(
            request.inbound_workflow_id, user.selected_organization_id
        )

    if request.telephony_trunk_id is not None:
        await _ensure_trunk_belongs_to_config(request.telephony_trunk_id, config_id)

    # The inbound routing key must stay unambiguous across every org — the rule,
    # and every path that has to honour it, live in services/telephony/inbound_routing.
    try:
        await assert_no_inbound_routing_conflict(
            provider=cfg.provider,
            credentials=cfg.credentials,
            addresses=[canonical_address(request.address, request.country_code)],
            organization_id=user.selected_organization_id,
        )
    except InboundRoutingConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _ensure_provider_phone_number(
        config_id,
        user.selected_organization_id,
        request.address,
        request.country_code,
    )

    try:
        row = await db_client.create_phone_number(
            organization_id=user.selected_organization_id,
            telephony_configuration_id=config_id,
            address=request.address,
            country_code=request.country_code,
            label=request.label,
            inbound_workflow_id=request.inbound_workflow_id,
            telephony_trunk_id=request.telephony_trunk_id,
            is_active=request.is_active,
            is_default_caller_id=request.is_default_caller_id,
            extra_metadata=request.extra_metadata,
        )
    except TelephonyPhoneNumberConflictError:
        raise HTTPException(
            status_code=409,
            detail="A phone number with this address already exists in the org.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    response = _phone_number_to_response(row)
    if request.inbound_workflow_id is not None:
        response.provider_sync = await _sync_inbound_for_phone_number(
            config_id,
            user.selected_organization_id,
            row.address,
            attach=row.is_active,
        )
    return response


@router.get(
    "/telephony-configs/{config_id}/phone-numbers/{phone_number_id}",
    response_model=PhoneNumberResponse,
)
async def get_phone_number(
    config_id: int,
    phone_number_id: int,
    user: UserModel = Depends(get_user),
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)

    row = await db_client.get_phone_number_for_config(phone_number_id, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Phone number not found")
    return _phone_number_to_response(row)


@router.put(
    "/telephony-configs/{config_id}/phone-numbers/{phone_number_id}",
    response_model=PhoneNumberResponse,
)
async def update_phone_number(
    config_id: int,
    phone_number_id: int,
    request: PhoneNumberUpdateRequest,
    user: UserModel = Depends(get_user),
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)

    existing = await db_client.get_phone_number_for_config(phone_number_id, config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Phone number not found")

    if request.inbound_workflow_id is not None:
        await _ensure_workflow_belongs_to_org(
            request.inbound_workflow_id, user.selected_organization_id
        )

    if request.telephony_trunk_id is not None:
        await _ensure_trunk_belongs_to_config(request.telephony_trunk_id, config_id)

    row = await db_client.update_phone_number(
        phone_number_id=phone_number_id,
        telephony_configuration_id=config_id,
        label=request.label,
        inbound_workflow_id=request.inbound_workflow_id,
        telephony_trunk_id=request.telephony_trunk_id,
        is_active=request.is_active,
        country_code=request.country_code,
        extra_metadata=request.extra_metadata,
        clear_inbound_workflow=request.clear_inbound_workflow,
        clear_trunk=request.clear_trunk,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Phone number not found")

    response = _phone_number_to_response(row)

    # Sync the provider application or address with the inbound
    # calling webhook address
    response.provider_sync = await _sync_inbound_for_phone_number(
        config_id,
        user.selected_organization_id,
        row.address,
        attach=row.inbound_workflow_id is not None and row.is_active,
    )
    return response


@router.post(
    "/telephony-configs/{config_id}/phone-numbers/{phone_number_id}/set-default-caller",
    response_model=PhoneNumberResponse,
)
async def set_default_caller_id(
    config_id: int,
    phone_number_id: int,
    user: UserModel = Depends(get_user),
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)

    row = await db_client.set_default_caller_id(phone_number_id, config_id)
    if not row:
        raise HTTPException(status_code=404, detail="Phone number not found")
    return _phone_number_to_response(row)


@router.delete("/telephony-configs/{config_id}/phone-numbers/{phone_number_id}")
async def delete_phone_number(
    config_id: int,
    phone_number_id: int,
    user: UserModel = Depends(get_user),
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    await _ensure_config_belongs_to_org(config_id, user.selected_organization_id)

    existing = await db_client.get_phone_number_for_config(phone_number_id, config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Phone number not found")

    provider_sync = await _sync_inbound_for_phone_number(
        config_id,
        user.selected_organization_id,
        existing.address,
        attach=False,
    )
    if not provider_sync.ok:
        raise HTTPException(
            status_code=502,
            detail=(
                provider_sync.message
                or "Provider rejected the phone-number detach request"
            ),
        )

    deleted = await db_client.delete_phone_number(phone_number_id, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Phone number not found")

    return {
        "message": "Phone number deleted",
        "provider_sync": provider_sync.model_dump(),
    }


class LangfuseCredentialsRequest(BaseModel):
    host: str
    public_key: str
    secret_key: str
    # Required: Langfuse v4 trace links are project-scoped, and the legacy
    # /trace/<id> form 404s without it.
    project_id: str = Field(min_length=1)


class LangfuseCredentialsResponse(BaseModel):
    host: str = ""
    public_key: str = ""
    secret_key: str = ""
    project_id: str = ""
    configured: bool = False


@router.get("/langfuse-credentials", response_model=LangfuseCredentialsResponse)
async def get_langfuse_credentials(user: UserModel = Depends(get_user)):
    """Get Langfuse credentials for the user's organization with masked sensitive fields."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )

    if not config or not config.value:
        return LangfuseCredentialsResponse()

    return LangfuseCredentialsResponse(
        host=config.value.get("host", ""),
        public_key=mask_key(config.value.get("public_key", "")),
        secret_key=mask_key(config.value.get("secret_key", "")),
        project_id=config.value.get("project_id", ""),
        configured=True,
    )


@router.post("/langfuse-credentials")
async def save_langfuse_credentials(
    request: LangfuseCredentialsRequest,
    user: UserModel = Depends(get_user),
):
    """Save Langfuse credentials for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    existing_config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )

    config_value = {
        "host": normalize_langfuse_host(request.host),
        "public_key": request.public_key,
        "secret_key": request.secret_key,
        "project_id": request.project_id.strip(),
    }

    # Preserve masked fields
    if existing_config and existing_config.value:
        if is_mask_of(request.public_key, existing_config.value.get("public_key", "")):
            config_value["public_key"] = existing_config.value["public_key"]
        if is_mask_of(request.secret_key, existing_config.value.get("secret_key", "")):
            config_value["secret_key"] = existing_config.value["secret_key"]

    await db_client.upsert_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
        config_value,
    )

    # Broadcast to all workers so every process updates its in-memory exporter
    await get_worker_sync_manager().broadcast(
        WorkerSyncEventType.LANGFUSE_CREDENTIALS,
        action="update",
        org_id=user.selected_organization_id,
    )

    return {"message": "Langfuse credentials saved successfully"}


@router.delete("/langfuse-credentials")
async def delete_langfuse_credentials(user: UserModel = Depends(get_user)):
    """Delete Langfuse credentials for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    deleted = await db_client.delete_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )

    if not deleted:
        raise HTTPException(status_code=404, detail="No Langfuse credentials found")

    # Broadcast to all workers so every process removes its in-memory exporter
    await get_worker_sync_manager().broadcast(
        WorkerSyncEventType.LANGFUSE_CREDENTIALS,
        action="delete",
        org_id=user.selected_organization_id,
    )

    return {"message": "Langfuse credentials deleted successfully"}


class RetryConfigResponse(BaseModel):
    enabled: bool
    max_retries: int
    retry_delay_seconds: int
    retry_on_busy: bool
    retry_on_no_answer: bool
    retry_on_voicemail: bool


class TimeSlotResponse(BaseModel):
    day_of_week: int
    start_time: str
    end_time: str


class ScheduleConfigResponse(BaseModel):
    enabled: bool
    timezone: str
    slots: List[TimeSlotResponse]


class CircuitBreakerConfigResponse(BaseModel):
    enabled: bool = False
    failure_threshold: float = 0.5
    window_seconds: int = 120
    min_calls_in_window: int = 5


class LastCampaignSettingsResponse(BaseModel):
    retry_config: Optional[RetryConfigResponse] = None
    max_concurrency: Optional[int] = None
    schedule_config: Optional[ScheduleConfigResponse] = None
    circuit_breaker: Optional[CircuitBreakerConfigResponse] = None


class CampaignDefaultsResponse(BaseModel):
    concurrent_call_limit: int
    from_numbers_count: int
    default_retry_config: RetryConfigResponse
    last_campaign_settings: Optional[LastCampaignSettingsResponse] = None


@router.get("/campaign-defaults", response_model=CampaignDefaultsResponse)
async def get_campaign_defaults(user: UserModel = Depends(get_user)):
    """Get campaign limits for the user's organization.

    Returns the organization's concurrent call limit and default retry configuration.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Get concurrent call limit
    concurrent_limit = DEFAULT_ORG_CONCURRENCY_LIMIT
    try:
        config = await db_client.get_configuration(
            user.selected_organization_id,
            OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
        )
        if config and config.value:
            concurrent_limit = int(
                config.value.get("value", DEFAULT_ORG_CONCURRENCY_LIMIT)
            )
    except Exception:
        pass

    # Phone-number count from the org's default telephony config (used by the
    # campaign UI to validate max_concurrency against caller-id supply).
    from_numbers_count = 0
    try:
        default_cfg = await db_client.get_default_telephony_configuration(
            user.selected_organization_id
        )
        if default_cfg:
            addresses = await db_client.list_active_normalized_addresses_for_config(
                default_cfg.id
            )
            from_numbers_count = len(addresses)
    except Exception:
        pass

    # Get last campaign settings for pre-population
    last_campaign_settings = None
    try:
        last_campaign = await db_client.get_latest_campaign(
            user.selected_organization_id
        )
        if last_campaign:
            retry = None
            if last_campaign.retry_config:
                retry = RetryConfigResponse(**last_campaign.retry_config)

            max_conc = None
            sched = None
            cb = CircuitBreakerConfigResponse()
            if last_campaign.orchestrator_metadata:
                max_conc = last_campaign.orchestrator_metadata.get("max_concurrency")
                sc = last_campaign.orchestrator_metadata.get("schedule_config")
                if sc:
                    sched = ScheduleConfigResponse(
                        enabled=sc.get("enabled", False),
                        timezone=sc.get("timezone", "UTC"),
                        slots=[
                            TimeSlotResponse(**slot) for slot in sc.get("slots", [])
                        ],
                    )
                cb_data = last_campaign.orchestrator_metadata.get("circuit_breaker")
                if cb_data:
                    cb = CircuitBreakerConfigResponse(**cb_data)
                else:
                    cb = CircuitBreakerConfigResponse()

            last_campaign_settings = LastCampaignSettingsResponse(
                retry_config=retry,
                max_concurrency=max_conc,
                schedule_config=sched,
                circuit_breaker=cb,
            )
    except Exception:
        pass

    return CampaignDefaultsResponse(
        concurrent_call_limit=concurrent_limit,
        from_numbers_count=from_numbers_count,
        default_retry_config=RetryConfigResponse(**DEFAULT_CAMPAIGN_RETRY_CONFIG),
        last_campaign_settings=last_campaign_settings,
    )
