from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import FolderModel, WorkflowModel
from api.enums import WorkflowStatus


class FolderNameConflictError(Exception):
    """Raised when a folder name already exists within the organization."""


class FolderClient(BaseDBClient):
    async def create_folder(self, name: str, organization_id: int) -> FolderModel:
        async with self.async_session() as session:
            folder = FolderModel(name=name, organization_id=organization_id)
            session.add(folder)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise FolderNameConflictError(
                    f"A folder named '{name}' already exists."
                )
            await session.refresh(folder)
        return folder

    async def get_folder(
        self, folder_id: int, organization_id: int
    ) -> FolderModel | None:
        """Fetch a single folder scoped to the organization (tenant isolation)."""
        async with self.async_session() as session:
            result = await session.execute(
                select(FolderModel).where(
                    FolderModel.id == folder_id,
                    FolderModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_folders(self, organization_id: int) -> list[FolderModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(FolderModel)
                .where(FolderModel.organization_id == organization_id)
                .order_by(FolderModel.name.asc())
            )
            return result.scalars().all()

    async def rename_folder(
        self, folder_id: int, name: str, organization_id: int
    ) -> FolderModel:
        async with self.async_session() as session:
            result = await session.execute(
                select(FolderModel).where(
                    FolderModel.id == folder_id,
                    FolderModel.organization_id == organization_id,
                )
            )
            folder = result.scalar_one_or_none()
            if folder is None:
                raise ValueError(f"Folder with id {folder_id} not found")

            folder.name = name
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise FolderNameConflictError(
                    f"A folder named '{name}' already exists."
                )
            await session.refresh(folder)
        return folder

    async def delete_folder(self, folder_id: int, organization_id: int) -> bool:
        """Delete a folder. Member workflows are un-filed (folder_id -> NULL)
        via the ON DELETE SET NULL foreign key, never deleted.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(FolderModel).where(
                    FolderModel.id == folder_id,
                    FolderModel.organization_id == organization_id,
                )
            )
            folder = result.scalar_one_or_none()
            if folder is None:
                return False

            await session.delete(folder)
            await session.commit()
        return True

    async def get_active_workflow_counts_by_folder(
        self, organization_id: int
    ) -> dict[int, int]:
        """Return {folder_id: active_workflow_count} for the organization.

        Only counts active (non-archived) workflows with a non-NULL folder_id.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(
                    WorkflowModel.folder_id,
                    func.count(WorkflowModel.id).label("count"),
                )
                .where(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowModel.folder_id.is_not(None),
                    WorkflowModel.status == WorkflowStatus.ACTIVE.value,
                )
                .group_by(WorkflowModel.folder_id)
            )
            return {folder_id: count for folder_id, count in result.all()}
