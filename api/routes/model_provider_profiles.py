"""CRUD routes for an organization's saved model provider profiles."""

from fastapi import APIRouter, Depends, HTTPException, Response

from api.db.models import UserModel
from api.schemas.model_provider_profiles import (
    ModelProviderProfileCreateRequest,
    ModelProviderProfileResponse,
    ModelProviderProfilesResponse,
    ModelProviderProfileUpdateRequest,
    ProfileService,
)
from api.services.auth.depends import get_user_with_selected_organization
from api.services.configuration import provider_profiles as profiles

router = APIRouter(
    prefix="/organizations/model-configurations/v2/profiles",
    tags=["organizations"],
)


@router.get("", response_model=ModelProviderProfilesResponse)
async def list_model_provider_profiles(
    user: UserModel = Depends(get_user_with_selected_organization),
) -> ModelProviderProfilesResponse:
    """List saved provider profiles. API keys are masked."""
    items = await profiles.list_profiles(user.selected_organization_id)
    return ModelProviderProfilesResponse(
        profiles=[profiles.mask_profile(p) for p in items]
    )


@router.post("", response_model=ModelProviderProfileResponse, status_code=201)
async def create_model_provider_profile(
    request: ModelProviderProfileCreateRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> ModelProviderProfileResponse:
    """Save a new named provider profile after validating its credentials."""
    try:
        created = await profiles.create_profile(
            user.selected_organization_id,
            name=request.name,
            service=request.service,
            config=request.config,
            created_by=user.provider_id,
        )
    except profiles.ProfileAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except profiles.ProfileConfigError as exc:
        raise HTTPException(status_code=422, detail=exc.detail)
    return profiles.mask_profile(created)


@router.put("/{service}/{name}", response_model=ModelProviderProfileResponse)
async def update_model_provider_profile(
    service: ProfileService,
    name: str,
    request: ModelProviderProfileUpdateRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> ModelProviderProfileResponse:
    """Replace a profile's config. Masked or omitted secrets keep stored values."""
    try:
        updated = await profiles.update_profile(
            user.selected_organization_id,
            service=service,
            name=name,
            config=request.config,
            created_by=user.provider_id,
        )
    except profiles.ProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except profiles.ProfileConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except profiles.ProfileConfigError as exc:
        raise HTTPException(status_code=422, detail=exc.detail)
    return profiles.mask_profile(updated)


@router.delete("/{service}/{name}", status_code=204)
async def delete_model_provider_profile(
    service: ProfileService,
    name: str,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> Response:
    try:
        await profiles.delete_profile(
            user.selected_organization_id, service=service, name=name
        )
    except profiles.ProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(status_code=204)
