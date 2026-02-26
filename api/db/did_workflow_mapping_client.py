from typing import Optional

from sqlalchemy import and_
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import DIDWorkflowMappingModel


class DIDWorkflowMappingClient(BaseDBClient):
    async def get_did_mappings(
        self, organization_id: int
    ) -> list[DIDWorkflowMappingModel]:
        """Get all DID mappings for an organization."""
        async with self.async_session() as session:
            result = await session.execute(
                select(DIDWorkflowMappingModel).where(
                    DIDWorkflowMappingModel.organization_id == organization_id
                )
            )
            return result.scalars().all()

    async def get_workflow_id_for_did(
        self, organization_id: int, did_number: str
    ) -> Optional[int]:
        """Look up the active workflow_id mapped to a DID number."""
        async with self.async_session() as session:
            result = await session.execute(
                select(DIDWorkflowMappingModel).where(
                    and_(
                        DIDWorkflowMappingModel.organization_id == organization_id,
                        DIDWorkflowMappingModel.did_number == did_number,
                        DIDWorkflowMappingModel.is_active == True,
                    )
                )
            )
            mapping = result.scalars().first()
            return mapping.workflow_id if mapping else None

    async def create_did_mapping(
        self, organization_id: int, did_number: str, workflow_id: int
    ) -> DIDWorkflowMappingModel:
        """Create a new DID-to-workflow mapping."""
        async with self.async_session() as session:
            mapping = DIDWorkflowMappingModel(
                organization_id=organization_id,
                did_number=did_number,
                workflow_id=workflow_id,
            )
            session.add(mapping)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(mapping)
            return mapping

    async def update_did_mapping(
        self,
        mapping_id: int,
        organization_id: int,
        did_number: Optional[str] = None,
        workflow_id: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[DIDWorkflowMappingModel]:
        """Update an existing DID mapping."""
        async with self.async_session() as session:
            result = await session.execute(
                select(DIDWorkflowMappingModel).where(
                    and_(
                        DIDWorkflowMappingModel.id == mapping_id,
                        DIDWorkflowMappingModel.organization_id == organization_id,
                    )
                )
            )
            mapping = result.scalars().first()
            if not mapping:
                return None

            if did_number is not None:
                mapping.did_number = did_number
            if workflow_id is not None:
                mapping.workflow_id = workflow_id
            if is_active is not None:
                mapping.is_active = is_active

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(mapping)
            return mapping

    async def delete_did_mapping(
        self, mapping_id: int, organization_id: int
    ) -> bool:
        """Delete a DID mapping. Returns True if deleted, False if not found."""
        async with self.async_session() as session:
            result = await session.execute(
                select(DIDWorkflowMappingModel).where(
                    and_(
                        DIDWorkflowMappingModel.id == mapping_id,
                        DIDWorkflowMappingModel.organization_id == organization_id,
                    )
                )
            )
            mapping = result.scalars().first()
            if not mapping:
                return False

            await session.delete(mapping)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            return True
