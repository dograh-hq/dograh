"""API routes for managing webhook credentials."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.enums import WebhookCredentialType
from api.sdk_expose import sdk_expose
from api.services import credential_service
from api.services.auth.depends import get_user
from api.services.credential_service import validate_credential_data

router = APIRouter(prefix="/credentials")


# Request/Response schemas
class CreateCredentialRequest(BaseModel):
    """Request schema for creating a webhook credential."""

    name: str
    description: Optional[str] = None
    credential_type: WebhookCredentialType
    credential_data: dict  # Validated based on credential_type


class UpdateCredentialRequest(BaseModel):
    """Request schema for updating a webhook credential."""

    name: Optional[str] = None
    description: Optional[str] = None
    credential_type: Optional[WebhookCredentialType] = None
    credential_data: Optional[dict] = None


class CredentialResponse(BaseModel):
    """Response schema for a webhook credential (never includes sensitive data)."""

    uuid: str
    name: str
    description: Optional[str]
    credential_type: WebhookCredentialType
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


def build_credential_response(credential) -> CredentialResponse:
    """Build a response from a credential model (excluding sensitive data)."""
    return CredentialResponse(
        uuid=credential.credential_uuid,
        name=credential.name,
        description=credential.description,
        credential_type=credential.credential_type,
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


@router.get(
    "/",
    **sdk_expose(
        method="list_credentials",
        description="List webhook credentials available to the authenticated organization.",
    ),
)
async def list_credentials(
    user: UserModel = Depends(get_user),
) -> List[CredentialResponse]:
    """
    List all webhook credentials for the user's organization.

    Returns:
        List of credentials (without sensitive data)
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    credentials = await db_client.get_credentials_for_organization(
        user.selected_organization_id
    )

    return [build_credential_response(cred) for cred in credentials]


@router.post("/")
async def create_credential(
    request: CreateCredentialRequest,
    user: UserModel = Depends(get_user),
) -> CredentialResponse:
    """
    Create a new webhook credential.

    Args:
        request: The credential creation request

    Returns:
        The created credential (without sensitive data)
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    # Validate credential data structure
    validate_credential_data(request.credential_type, request.credential_data)

    try:
        credential = await db_client.create_credential(
            organization_id=user.selected_organization_id,
            user_id=user.id,
            name=request.name,
            description=request.description,
            credential_type=request.credential_type.value,
            credential_data=request.credential_data,
        )

        return build_credential_response(credential)

    except Exception as e:
        # Handle unique constraint violation
        if "unique_org_credential_name" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"A credential with the name '{request.name}' already exists",
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{credential_uuid}")
async def get_credential(
    credential_uuid: str,
    user: UserModel = Depends(get_user),
) -> CredentialResponse:
    """
    Get a specific webhook credential by UUID.

    Args:
        credential_uuid: The UUID of the credential

    Returns:
        The credential (without sensitive data)
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    credential = await db_client.get_credential_by_uuid(
        credential_uuid, user.selected_organization_id
    )

    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")

    return build_credential_response(credential)


@router.put("/{credential_uuid}")
async def update_credential(
    credential_uuid: str,
    request: UpdateCredentialRequest,
    user: UserModel = Depends(get_user),
) -> CredentialResponse:
    """
    Update a webhook credential.

    Args:
        credential_uuid: The UUID of the credential to update
        request: The update request

    Returns:
        The updated credential (without sensitive data)
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    try:
        # Pass through to service which handles validation and cache invalidation
        credential = await credential_service.update_credential_with_invalidation(
            credential_uuid=credential_uuid,
            organization_id=user.selected_organization_id,
            name=request.name,
            description=request.description,
            credential_type=request.credential_type,
            credential_data=request.credential_data,
        )

        if not credential:
            raise HTTPException(status_code=404, detail="Credential not found")

        return build_credential_response(credential)

    except HTTPException:
        raise
    except Exception as e:
        if "unique_org_credential_name" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"A credential with the name '{request.name}' already exists",
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{credential_uuid}")
async def delete_credential(
    credential_uuid: str,
    user: UserModel = Depends(get_user),
) -> dict:
    """
    Delete (soft delete) a webhook credential.

    Args:
        credential_uuid: The UUID of the credential to delete

    Returns:
        Success message
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    deleted = await credential_service.delete_credential_with_invalidation(
        credential_uuid, user.selected_organization_id
    )

    if not deleted:
        raise HTTPException(status_code=404, detail="Credential not found")

    return {"status": "deleted", "uuid": credential_uuid}
