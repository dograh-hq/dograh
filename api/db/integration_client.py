from typing import List

from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import IntegrationModel


class IntegrationClient(BaseDBClient):
    async def get_integrations_by_organization_id(
        self, organization_id: int
    ) -> list[IntegrationModel]:
        """Get all integrations for a specific organization."""
        async with self.async_session() as session:
            result = await session.execute(
                select(IntegrationModel).where(
                    IntegrationModel.organization_id == organization_id
                )
            )
            return result.scalars().all()

    async def create_integration(
        self,
        integration_id: str,
        provider: str,
        organization_id: int,
        connection_details: dict,
        created_by: int = None,
        is_active: bool = True,
    ) -> IntegrationModel:
        """Create a new integration for an organization."""
        async with self.async_session() as session:
            new_integration = IntegrationModel(
                integration_id=integration_id,
                organization_id=organization_id,
                created_by=created_by,
                is_active=is_active,
                provider=provider,
                connection_details=connection_details,
            )
            session.add(new_integration)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(new_integration)
            return new_integration

    async def update_integration_status(
        self, integration_id: int, is_active: bool
    ) -> IntegrationModel | None:
        """Update the active status of an integration."""
        async with self.async_session() as session:
            result = await session.execute(
                select(IntegrationModel).where(IntegrationModel.id == integration_id)
            )
            integration = result.scalars().first()
            if not integration:
                return None

            integration.is_active = is_active
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(integration)
            return integration

    async def update_integration_connection_details(
        self, integration_id: int, connection_details: dict
    ) -> IntegrationModel | None:
        """Update the connection details of an integration."""
        async with self.async_session() as session:
            result = await session.execute(
                select(IntegrationModel).where(IntegrationModel.id == integration_id)
            )
            integration = result.scalars().first()
            if not integration:
                return None

            integration.connection_details = connection_details
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(integration)
            return integration

    async def get_active_integrations_by_organization(
        self, organization_id: int
    ) -> List[IntegrationModel]:
        """Get all active integrations for a specific organization."""
        async with self.async_session() as session:
            result = await session.execute(
                select(IntegrationModel).where(
                    IntegrationModel.organization_id == organization_id,
                    IntegrationModel.is_active == True,
                )
            )
            return result.scalars().all()
