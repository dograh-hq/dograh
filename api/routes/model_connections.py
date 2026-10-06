"""Authenticated, organization-scoped model connection management."""

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from api.db import db_client
from api.db.model_connection_client import ModelCatalogConflict, ModelCatalogNotFound
from api.db.models import UserModel
from api.schemas.model_connections import (
    DefaultModelConfigurationRequest,
    DefaultModelConfigurationResponse,
    ModelConfigurationOverride,
    ModelConfigurationPreview,
    NamedModelConfigurationCreate,
    NamedModelConfigurationResponse,
    NamedModelConfigurationUpdate,
    ProviderConnectionCreate,
    ProviderConnectionResponse,
    ProviderConnectionUpdate,
)
from api.services.auth.depends import get_user_with_selected_organization
from api.services.configuration.model_connections import (
    get_default_model_configuration as get_default,
)
from api.services.configuration.model_connections import (
    model_connection_catalog,
    public_snapshot,
    resolve_inline_model_configuration,
    resolve_model_configuration,
    validate_embedding_compatibility,
    validate_provider_connection,
    validate_provider_connection_credentials,
)


class SecretSafeRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                # FastAPI otherwise echoes submitted credentials in validation
                # error inputs, including a complete body for model validators.
                raise HTTPException(
                    status_code=422,
                    detail=[
                        {key: error[key] for key in ("loc", "msg", "type")}
                        for error in exc.errors()
                    ],
                ) from None

        return handler


router = APIRouter(
    prefix="/model-connections", tags=["model-connections"], route_class=SecretSafeRoute
)
Auth = Depends(get_user_with_selected_organization)


def _connection_response(row):
    return ProviderConnectionResponse(
        uuid=row.uuid,
        name=row.name,
        provider=row.provider,
        connection_settings=row.connection_settings,
        configured_credentials=sorted(
            key for key, value in row.credentials.items() if value
        ),
        revision=row.revision,
        is_active=row.is_active,
    )


def _configuration_response(row):
    return NamedModelConfigurationResponse(
        uuid=row.uuid,
        name=row.name,
        configuration=row.configuration,
        revision=row.revision,
        is_active=row.is_active,
    )


async def _write(operation):
    try:
        return await operation
    except ModelCatalogNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ModelCatalogConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/catalog", operation_id="get_model_connection_catalog")
async def get_catalog(user: UserModel = Auth) -> dict:
    return model_connection_catalog()


@router.get(
    "/provider-connections",
    response_model=list[ProviderConnectionResponse],
    operation_id="list_provider_connections",
)
async def list_connections(user: UserModel = Auth, include_archived: bool = False):
    return [
        _connection_response(row)
        for row in await db_client.list_provider_connections(
            user.selected_organization_id, active_only=not include_archived
        )
    ]


@router.post(
    "/provider-connections",
    response_model=ProviderConnectionResponse,
    status_code=201,
    operation_id="create_provider_connection",
)
async def create_connection(request: ProviderConnectionCreate, user: UserModel = Auth):
    await validate_provider_connection_credentials(
        request.provider,
        request.credentials,
        request.connection_settings,
        organization_id=user.selected_organization_id,
        created_by=user.provider_id,
    )
    row = await _write(
        db_client.create_provider_connection(
            user.selected_organization_id, **request.model_dump()
        )
    )
    return _connection_response(row)


@router.patch(
    "/provider-connections/{connection_uuid}",
    response_model=ProviderConnectionResponse,
    operation_id="update_provider_connection",
)
async def update_connection(
    connection_uuid: str, request: ProviderConnectionUpdate, user: UserModel = Auth
):
    row = await db_client.get_provider_connection(
        user.selected_organization_id, connection_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    changes = request.model_dump(exclude_unset=True)
    revision = changes.pop("revision", row.revision)
    if revision != row.revision:
        raise HTTPException(
            status_code=409, detail="Provider connection changed; refresh and retry"
        )
    credentials = {**row.credentials, **changes.get("credentials", {})}
    settings = changes.get("connection_settings", row.connection_settings)
    if changes.get("credentials") or settings != row.connection_settings:
        await validate_provider_connection_credentials(
            row.provider,
            credentials,
            settings,
            organization_id=user.selected_organization_id,
            created_by=user.provider_id,
        )
    else:
        validate_provider_connection(row.provider, credentials, settings)
    updated = await _write(
        db_client.update_provider_connection(
            user.selected_organization_id,
            connection_uuid,
            changes=changes,
            expected_revision=revision,
        )
    )
    return _connection_response(updated)


@router.delete(
    "/provider-connections/{connection_uuid}",
    status_code=204,
    operation_id="archive_provider_connection",
)
async def archive_connection(connection_uuid: str, user: UserModel = Auth):
    await _write(
        db_client.archive_provider_connection(
            user.selected_organization_id, connection_uuid
        )
    )
    return Response(status_code=204)


@router.post(
    "/provider-connections/{connection_uuid}/restore",
    response_model=ProviderConnectionResponse,
    operation_id="restore_provider_connection",
)
async def restore_connection(connection_uuid: str, user: UserModel = Auth):
    row = await _write(
        db_client.restore_provider_connection(
            user.selected_organization_id, connection_uuid
        )
    )
    return _connection_response(row)


@router.get(
    "/model-configurations",
    response_model=list[NamedModelConfigurationResponse],
    operation_id="list_named_model_configurations",
)
async def list_configurations(user: UserModel = Auth, include_archived: bool = False):
    return [
        _configuration_response(row)
        for row in await db_client.list_named_model_configurations(
            user.selected_organization_id, active_only=not include_archived
        )
    ]


@router.post(
    "/model-configurations",
    response_model=NamedModelConfigurationResponse,
    status_code=201,
    operation_id="create_named_model_configuration",
)
async def create_configuration(
    request: NamedModelConfigurationCreate, user: UserModel = Auth
):
    await resolve_inline_model_configuration(
        user.selected_organization_id, request.configuration
    )
    row = await _write(
        db_client.create_named_model_configuration(
            user.selected_organization_id, **request.model_dump(mode="json")
        )
    )
    return _configuration_response(row)


@router.get(
    "/model-configurations/{configuration_uuid}",
    response_model=NamedModelConfigurationResponse,
    operation_id="get_named_model_configuration",
)
async def get_configuration(configuration_uuid: str, user: UserModel = Auth):
    row = await db_client.get_named_model_configuration(
        user.selected_organization_id, configuration_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    return _configuration_response(row)


@router.patch(
    "/model-configurations/{configuration_uuid}",
    response_model=NamedModelConfigurationResponse,
    operation_id="update_named_model_configuration",
)
async def update_configuration(
    configuration_uuid: str,
    request: NamedModelConfigurationUpdate,
    user: UserModel = Auth,
):
    existing = await db_client.get_named_model_configuration(
        user.selected_organization_id, configuration_uuid
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    changes = request.model_dump(mode="json", exclude_unset=True)
    revision = changes.pop("revision", existing.revision)
    if request.configuration is not None:
        resolved = await resolve_inline_model_configuration(
            user.selected_organization_id, request.configuration
        )
        default = await get_default(user.selected_organization_id)
        if (
            default is not None
            and default.uuid == configuration_uuid
            and existing.configuration.get("embeddings")
            != changes["configuration"].get("embeddings")
        ):
            await validate_embedding_compatibility(
                user.selected_organization_id, resolved.effective
            )
    row = await _write(
        db_client.update_named_model_configuration(
            user.selected_organization_id,
            configuration_uuid,
            changes=changes,
            expected_revision=revision,
        )
    )
    return _configuration_response(row)


@router.delete(
    "/model-configurations/{configuration_uuid}",
    status_code=204,
    operation_id="archive_named_model_configuration",
)
async def archive_configuration(configuration_uuid: str, user: UserModel = Auth):
    await _write(
        db_client.archive_named_model_configuration(
            user.selected_organization_id, configuration_uuid
        )
    )
    return Response(status_code=204)


@router.post(
    "/model-configurations/{configuration_uuid}/restore",
    response_model=NamedModelConfigurationResponse,
    operation_id="restore_named_model_configuration",
)
async def restore_configuration(configuration_uuid: str, user: UserModel = Auth):
    row = await db_client.get_named_model_configuration(
        user.selected_organization_id, configuration_uuid, active_only=False
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    await resolve_inline_model_configuration(
        user.selected_organization_id, row.configuration
    )
    restored = await _write(
        db_client.restore_named_model_configuration(
            user.selected_organization_id,
            configuration_uuid,
            expected_revision=row.revision,
        )
    )
    return _configuration_response(restored)


@router.get(
    "/default",
    response_model=DefaultModelConfigurationResponse,
    operation_id="get_default_model_configuration",
)
async def get_default_configuration(user: UserModel = Auth):
    row = await get_default(user.selected_organization_id)
    return DefaultModelConfigurationResponse(
        model_configuration_uuid=row.uuid if row else None
    )


@router.put(
    "/default",
    response_model=DefaultModelConfigurationResponse,
    operation_id="set_default_model_configuration",
)
async def set_default_configuration(
    request: DefaultModelConfigurationRequest, user: UserModel = Auth
):
    row = await db_client.get_named_model_configuration(
        user.selected_organization_id, str(request.model_configuration_uuid)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    resolved = await resolve_inline_model_configuration(
        user.selected_organization_id, row.configuration
    )
    await validate_embedding_compatibility(
        user.selected_organization_id, resolved.effective
    )
    await _write(
        db_client.set_default_named_model_configuration(
            user.selected_organization_id,
            str(request.model_configuration_uuid),
            expected_revision=row.revision,
        )
    )
    return request


@router.post(
    "/resolve",
    response_model=ModelConfigurationPreview,
    operation_id="preview_model_configuration",
)
async def preview_configuration(
    request: ModelConfigurationOverride, user: UserModel = Auth
):
    resolved = await resolve_model_configuration(
        user.selected_organization_id, api_override=request
    )
    return ModelConfigurationPreview(
        configuration=public_snapshot(resolved.snapshot), provenance=resolved.provenance
    )
