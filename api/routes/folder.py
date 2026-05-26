from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from api.db import db_client
from api.db.folder_client import FolderNameConflictError
from api.db.models import UserModel
from api.services.auth.depends import get_user

router = APIRouter(prefix="/folder")


class FolderResponse(BaseModel):
    id: int
    name: str
    created_at: datetime


class CreateFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Folder name cannot be empty")
        return v


class UpdateFolderRequest(CreateFolderRequest):
    pass


@router.get("/")
async def list_folders(
    user: UserModel = Depends(get_user),
) -> list[FolderResponse]:
    """List all folders in the authenticated user's organization."""
    folders = await db_client.list_folders(
        organization_id=user.selected_organization_id
    )
    return [
        FolderResponse(id=f.id, name=f.name, created_at=f.created_at) for f in folders
    ]


@router.post("/")
async def create_folder(
    request: CreateFolderRequest,
    user: UserModel = Depends(get_user),
) -> FolderResponse:
    """Create a new folder in the authenticated user's organization."""
    try:
        folder = await db_client.create_folder(
            name=request.name,
            organization_id=user.selected_organization_id,
        )
    except FolderNameConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return FolderResponse(id=folder.id, name=folder.name, created_at=folder.created_at)


@router.put("/{folder_id}")
async def rename_folder(
    folder_id: int,
    request: UpdateFolderRequest,
    user: UserModel = Depends(get_user),
) -> FolderResponse:
    """Rename a folder owned by the authenticated user's organization."""
    try:
        folder = await db_client.rename_folder(
            folder_id=folder_id,
            name=request.name,
            organization_id=user.selected_organization_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FolderNameConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return FolderResponse(id=folder.id, name=folder.name, created_at=folder.created_at)


@router.delete("/{folder_id}")
async def delete_folder(
    folder_id: int,
    user: UserModel = Depends(get_user),
) -> dict[str, bool]:
    """Delete a folder. Member agents are moved to "Uncategorized", not deleted."""
    deleted = await db_client.delete_folder(
        folder_id=folder_id,
        organization_id=user.selected_organization_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Folder with id {folder_id} not found"
        )
    return {"success": True}
