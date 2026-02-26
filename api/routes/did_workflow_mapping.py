from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user

router = APIRouter(prefix="/did-mappings", tags=["did-mappings"])


class DIDMappingResponse(BaseModel):
    id: int
    organization_id: int
    did_number: str
    workflow_id: int
    is_active: bool
    created_at: datetime


class CreateDIDMappingRequest(BaseModel):
    did_number: str
    workflow_id: int


class UpdateDIDMappingRequest(BaseModel):
    did_number: Optional[str] = None
    workflow_id: Optional[int] = None
    is_active: Optional[bool] = None


def _to_response(mapping) -> DIDMappingResponse:
    return DIDMappingResponse(
        id=mapping.id,
        organization_id=mapping.organization_id,
        did_number=mapping.did_number,
        workflow_id=mapping.workflow_id,
        is_active=mapping.is_active,
        created_at=mapping.created_at,
    )


@router.get("/", response_model=list[DIDMappingResponse])
async def list_did_mappings(user: UserModel = Depends(get_user)):
    """List all DID-to-workflow mappings for the current organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    mappings = await db_client.get_did_mappings(user.selected_organization_id)
    return [_to_response(m) for m in mappings]


@router.post("/", response_model=DIDMappingResponse, status_code=201)
async def create_did_mapping(
    request: CreateDIDMappingRequest,
    user: UserModel = Depends(get_user),
):
    """Create a new DID-to-workflow mapping."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    try:
        mapping = await db_client.create_did_mapping(
            organization_id=user.selected_organization_id,
            did_number=request.did_number,
            workflow_id=request.workflow_id,
        )
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err:
            raise HTTPException(
                status_code=409,
                detail=f"DID {request.did_number} is already mapped in this organization",
            )
        raise HTTPException(status_code=500, detail="Failed to create DID mapping")
    return _to_response(mapping)


@router.put("/{mapping_id}", response_model=DIDMappingResponse)
async def update_did_mapping(
    mapping_id: int,
    request: UpdateDIDMappingRequest,
    user: UserModel = Depends(get_user),
):
    """Update an existing DID mapping (number, workflow, or active status)."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    mapping = await db_client.update_did_mapping(
        mapping_id=mapping_id,
        organization_id=user.selected_organization_id,
        did_number=request.did_number,
        workflow_id=request.workflow_id,
        is_active=request.is_active,
    )
    if not mapping:
        raise HTTPException(status_code=404, detail="DID mapping not found")
    return _to_response(mapping)


@router.delete("/{mapping_id}")
async def delete_did_mapping(
    mapping_id: int,
    user: UserModel = Depends(get_user),
):
    """Delete a DID mapping."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    deleted = await db_client.delete_did_mapping(
        mapping_id=mapping_id,
        organization_id=user.selected_organization_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="DID mapping not found")
    return {"success": True}
