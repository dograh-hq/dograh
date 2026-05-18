from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from api.constants import DEFAULT_CAMPAIGN_RETRY_CONFIG, DEFAULT_ORG_CONCURRENCY_LIMIT
from api.db import db_client
from api.db.models import UserModel
from api.db.telephony_configuration_client import TelephonyConfigurationInUseError
from api.enums import OrganizationConfigurationKey, PostHogEvent
from api.schemas.telephony_config import (
    TelephonyConfigRequest,
    TelephonyConfigurationCreateRequest,
    TelephonyConfigurationDetail,
    TelephonyConfigurationListItem,
    TelephonyConfigurationListResponse,
    TelephonyConfigurationResponse,
    TelephonyConfigurationUpdateRequest,
)
from api.schemas.telephony_phone_number import (
    PhoneNumberCreateRequest,
    PhoneNumberListResponse,
    PhoneNumberResponse,
    PhoneNumberUpdateRequest,
    ProviderSyncStatus,
)
from api.services.auth.depends import get_user
from api.services.configuration.masking import is_mask_of, mask_key
from api.services.posthog_client import capture_event
from api.services.telephony import registry as telephony_registry
from api.services.telephony.factory import get_telephony_provider_by_id
from api.services.worker_sync.manager import get_worker_sync_manager
from api.services.worker_sync.protocol import WorkerSyncEventType
from api.utils.common import get_backend_endpoints

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


def _mask_sensitive(provider_name: str, value: dict) -> dict:
    """Return a copy of ``value`` with sensitive fields masked for display."""
    out = dict(value)
    for field_name in _sensitive_fields(provider_name):
        v = out.get(field_name)
        if v:
            out[field_name] = mask_key(v)
    return out


class TelephonyProviderUIField(BaseModel):
    """One form field on a telephony provider's configuration UI."""

    name: str
    label: str
    type: str
    required: bool
    sensitive: bool
    description: Optional[str] = None
    placeholder: Optional[str] = None


class TelephonyProviderMetadata(BaseModel):
    """UI form metadata for a single telephony provider."""

    provider: str
    display_name: str
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

    providers = []
    for spec in telephony_registry.all_specs():
        if spec.ui_metadata is None:
            continue
        providers.append(
            TelephonyProviderMetadata(
                provider=spec.name,
                display_name=spec.ui_metadata.display_name,
                fields=[
                    TelephonyProviderUIField(
                        name=f.name,
                        label=f.label,
                        type=f.type,
                        required=f.required,
                        sensitive=f.sensitive,
                        description=f.description,
                        placeholder=f.placeholder,
                    )
                    for f in spec.ui_metadata.fields
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

    Today this surfaces only Telnyx configs missing ``webhook_public_key``;
    additional warning types should be added as new fields on the response.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    telnyx_missing = await db_client.count_telnyx_configs_missing_webhook_public_key(
        user.selected_organization_id
    )
    return TelephonyConfigWarningsResponse(
        telnyx_missing_webhook_public_key_count=telnyx_missing,
    )


def preserve_masked_fields(provider: str, request_dict: dict, existing: dict):
    """If the client re-submitted a masked sensitive field, restore the original."""
    for field_name in _sensitive_fields(provider):
        v = request_dict.get(field_name)
        if v and is_mask_of(v, existing.get(field_name, "")):
            request_dict[field_name] = existing[field_name]


def _credentials_from_payload(config: TelephonyConfigRequest) -> dict:
    """Provider credentials only — strip provider/from_numbers from the payload."""
    payload = config.model_dump()
    payload.pop("provider", None)
    payload.pop("from_numbers", None)
    return payload


async def _run_preprocess_hook(provider: str, credentials: dict) -> dict:
    """Invoke the provider's optional credentials preprocessor before save."""
    spec = telephony_registry.get_optional(provider)
    if spec and spec.preprocess_credentials_on_save:
        return await spec.preprocess_credentials_on_save(credentials)
    return credentials


def _phone_number_to_response(
    row, inbound_workflow_name: Optional[str] = None
) -> PhoneNumberResponse:
    response = PhoneNumberResponse.model_validate(row)
    response.inbound_workflow_name = inbound_workflow_name
    return response


async def _sync_inbound_for_phone_number(
    config_id: int, organization_id: int, address: str
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
        items.append(
            TelephonyConfigurationListItem(
                id=row.id,
                name=row.name,
                provider=row.provider,
                is_default_outbound=row.is_default_outbound,
                phone_number_count=len([n for n in numbers if n.is_active]),
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
    except IntegrityError as e:
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

    return _detail_response(row)


@router.get(
    "/telephony-configs/{config_id}", response_model=TelephonyConfigurationDetail
)
async def get_telephony_configuration_by_id(
    config_id: int, user: UserModel = Depends(get_user)
):
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    row = await db_client.get_telephony_configuration_for_org(
        config_id, user.selected_organization_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")
    return _detail_response(row)


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
        config_id, user.selected_organization_id
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
        credentials = await _run_preprocess_hook(existing.provider, credentials)

    row = await db_client.update_telephony_configuration(
        config_id=config_id,
        organization_id=user.selected_organization_id,
        name=request.name,
        credentials=credentials,
    )

    return _detail_response(row)


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
    return _detail_response(row)


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


def _detail_response(row) -> TelephonyConfigurationDetail:
    masked = _mask_sensitive(row.provider, row.credentials or {})
    return TelephonyConfigurationDetail(
        id=row.id,
        name=row.name,
        provider=row.provider,
        is_default_outbound=row.is_default_outbound,
        credentials=masked,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Phone numbers (nested under a config)
# ---------------------------------------------------------------------------


async def _ensure_config_belongs_to_org(config_id: int, organization_id: int):
    cfg = await db_client.get_telephony_configuration_for_org(
        config_id, organization_id
    )
    if not cfg:
        raise HTTPException(status_code=404, detail="Telephony configuration not found")
    return cfg


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

    # Inbound dispatch (find_inbound_route_by_account) keys on (provider,
    # credentials[account_id_field], address_normalized) without the org, so
    # that tuple has to be globally unique. Reject up front if another config —
    # in this org or any other — already owns the same combination.
    spec = telephony_registry.get_optional(cfg.provider)
    account_field = spec.account_id_credential_field if spec else ""
    account_id = (cfg.credentials or {}).get(account_field) if account_field else None
    if account_id:
        try:
            conflict = await db_client.find_inbound_routing_conflict(
                provider=cfg.provider,
                account_id_field=account_field,
                account_id=account_id,
                address=request.address,
                country_hint=request.country_code,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if conflict:
            existing_cfg, existing_phone = conflict
            same_org = existing_cfg.organization_id == user.selected_organization_id
            scope = (
                f"telephony configuration '{existing_cfg.name}'"
                if same_org
                else "another organization using the same provider account"
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Phone number {existing_phone.address} is already registered "
                    f"under {scope}. Inbound calls cannot be uniquely routed when "
                    f"the same number is configured against the same provider "
                    f"account in more than one place."
                ),
            )

    try:
        row = await db_client.create_phone_number(
            organization_id=user.selected_organization_id,
            telephony_configuration_id=config_id,
            address=request.address,
            country_code=request.country_code,
            label=request.label,
            inbound_workflow_id=request.inbound_workflow_id,
            is_active=request.is_active,
            is_default_caller_id=request.is_default_caller_id,
            extra_metadata=request.extra_metadata,
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A phone number with this address already exists in the org.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    response = _phone_number_to_response(row)
    if request.inbound_workflow_id is not None:
        response.provider_sync = await _sync_inbound_for_phone_number(
            config_id, user.selected_organization_id, row.address
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

    row = await db_client.update_phone_number(
        phone_number_id=phone_number_id,
        telephony_configuration_id=config_id,
        label=request.label,
        inbound_workflow_id=request.inbound_workflow_id,
        is_active=request.is_active,
        country_code=request.country_code,
        extra_metadata=request.extra_metadata,
        clear_inbound_workflow=request.clear_inbound_workflow,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Phone number not found")

    response = _phone_number_to_response(row)

    # Sync the provider application or address with the inbound
    # calling webhook address
    response.provider_sync = await _sync_inbound_for_phone_number(
        config_id, user.selected_organization_id, row.address
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

    deleted = await db_client.delete_phone_number(phone_number_id, config_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Phone number not found")

    return {"message": "Phone number deleted"}


# ---------------------------------------------------------------------------
# Legacy single-config shim
# ---------------------------------------------------------------------------


@router.get("/telephony-config", response_model=TelephonyConfigurationResponse)
async def get_telephony_configuration(user: UserModel = Depends(get_user)):
    """Legacy: returns the org's default config in the original per-provider
    response shape so the existing single-form UI keeps working. Prefer the
    multi-config endpoints (``/telephony-configs``) for new clients.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    cfg = await db_client.get_default_telephony_configuration(
        user.selected_organization_id
    )
    if not cfg:
        return TelephonyConfigurationResponse()

    spec = telephony_registry.get_optional(cfg.provider)
    if spec is None:
        return TelephonyConfigurationResponse()

    addresses = await db_client.list_active_normalized_addresses_for_config(cfg.id)
    masked = _mask_sensitive(cfg.provider, cfg.credentials or {})
    payload = {**masked, "provider": cfg.provider, "from_numbers": addresses}
    response_obj = spec.config_response_cls.model_validate(payload)
    return TelephonyConfigurationResponse(**{cfg.provider: response_obj})


@router.post("/telephony-config")
async def save_telephony_configuration(
    request: TelephonyConfigRequest,
    user: UserModel = Depends(get_user),
):
    """Legacy: upserts the org's default config (and its phone numbers) in the
    original payload shape so existing UI clients keep working. Prefer the
    multi-config + phone-number endpoints for new clients.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    payload = request.model_dump()
    new_addresses = payload.pop("from_numbers", []) or []
    payload.pop("provider", None)

    default = await db_client.get_default_telephony_configuration(
        user.selected_organization_id
    )

    if default and default.provider == request.provider:
        preserve_masked_fields(request.provider, payload, default.credentials or {})
        row = await db_client.update_telephony_configuration(
            config_id=default.id,
            organization_id=user.selected_organization_id,
            credentials=payload,
        )
    else:
        row = await db_client.create_telephony_configuration(
            organization_id=user.selected_organization_id,
            name=f"{request.provider.title()} Default",
            provider=request.provider,
            credentials=payload,
            is_default_outbound=True,
        )

    # Replace the phone-number set with the inline payload.
    existing_numbers = await db_client.list_phone_numbers_for_config(row.id)
    existing_by_address = {n.address: n for n in existing_numbers}
    incoming_set = set(new_addresses)
    for addr in new_addresses:
        if addr in existing_by_address:
            continue
        try:
            await db_client.create_phone_number(
                organization_id=user.selected_organization_id,
                telephony_configuration_id=row.id,
                address=addr,
            )
        except IntegrityError:
            logger.warning(
                f"Skipping duplicate phone number {addr!r} for config {row.id}"
            )
        except ValueError as e:
            logger.warning(f"Skipping invalid phone number {addr!r}: {e}")
    for n in existing_numbers:
        if n.address not in incoming_set:
            await db_client.delete_phone_number(n.id, row.id)

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.TELEPHONY_CONFIGURED,
        properties={
            "provider": request.provider,
            "phone_number_count": len(new_addresses),
            "organization_id": user.selected_organization_id,
        },
    )

    return {"message": "Telephony configuration saved successfully"}


class LangfuseCredentialsRequest(BaseModel):
    host: str
    public_key: str
    secret_key: str


class LangfuseCredentialsResponse(BaseModel):
    host: str = ""
    public_key: str = ""
    secret_key: str = ""
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
        "host": request.host,
        "public_key": request.public_key,
        "secret_key": request.secret_key,
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
