"""Database client for managing webhook credentials."""

from datetime import UTC, datetime
from typing import List, Optional

from loguru import logger
from sqlalchemy import select, update

from api.db.base_client import BaseDBClient
from api.db.models import ExternalCredentialModel


class WebhookCredentialClient(BaseDBClient):
    """Client for managing webhook credentials (organization-scoped, UUID-referenced)."""

    async def create_credential(
        self,
        organization_id: int,
        user_id: int,
        name: str,
        credential_type: str,
        credential_data: dict,
        description: Optional[str] = None,
    ) -> ExternalCredentialModel:
        """Create a new webhook credential.

        Args:
            organization_id: ID of the organization
            user_id: ID of the user creating the credential
            name: Display name for the credential
            credential_type: Type of credential (none, api_key, bearer_token, basic_auth, custom_header)
            credential_data: JSON data containing the credential details
            description: Optional description

        Returns:
            The created ExternalCredentialModel with auto-generated UUID
        """
        async with self.async_session() as session:
            credential = ExternalCredentialModel(
                organization_id=organization_id,
                created_by=user_id,
                name=name,
                description=description,
                credential_type=credential_type,
                credential_data=credential_data,
            )

            session.add(credential)
            await session.commit()
            await session.refresh(credential)

            logger.info(
                f"Created webhook credential '{name}' ({credential.credential_uuid}) "
                f"for organization {organization_id}"
            )
            return credential

    async def get_credentials_for_organization(
        self, organization_id: int, active_only: bool = True
    ) -> List[ExternalCredentialModel]:
        """Get all credentials for an organization.

        Args:
            organization_id: ID of the organization
            active_only: If True, only return active (non-deleted) credentials

        Returns:
            List of ExternalCredentialModel instances
        """
        async with self.async_session() as session:
            query = select(ExternalCredentialModel).where(
                ExternalCredentialModel.organization_id == organization_id
            )

            if active_only:
                query = query.where(ExternalCredentialModel.is_active.is_(True))

            query = query.order_by(ExternalCredentialModel.name)

            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_credential_by_uuid(
        self, credential_uuid: str, organization_id: int, active_only: bool = True
    ) -> Optional[ExternalCredentialModel]:
        """Get a credential by its UUID, scoped to organization.

        Args:
            credential_uuid: The unique credential UUID
            organization_id: ID of the organization (for authorization)
            active_only: If True, only return if active

        Returns:
            ExternalCredentialModel if found and authorized, None otherwise
        """
        async with self.async_session() as session:
            query = select(ExternalCredentialModel).where(
                ExternalCredentialModel.credential_uuid == credential_uuid,
                ExternalCredentialModel.organization_id == organization_id,
            )

            if active_only:
                query = query.where(ExternalCredentialModel.is_active.is_(True))

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def update_credential(
        self,
        credential_uuid: str,
        organization_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        credential_type: Optional[str] = None,
        credential_data: Optional[dict] = None,
    ) -> Optional[ExternalCredentialModel]:
        """Update a credential by UUID.

        Args:
            credential_uuid: The unique credential UUID
            organization_id: ID of the organization (for authorization)
            name: New name (if provided)
            description: New description (if provided)
            credential_type: New credential type (if provided)
            credential_data: New credential data (if provided)

        Returns:
            Updated ExternalCredentialModel if found, None otherwise
        """
        async with self.async_session() as session:
            # First check if credential exists and belongs to organization
            credential = await self.get_credential_by_uuid(
                credential_uuid, organization_id
            )
            if not credential:
                return None

            # Build update values
            update_values = {"updated_at": datetime.now(UTC)}
            if name is not None:
                update_values["name"] = name
            if description is not None:
                update_values["description"] = description
            if credential_type is not None:
                update_values["credential_type"] = credential_type
            if credential_data is not None:
                update_values["credential_data"] = credential_data

            await session.execute(
                update(ExternalCredentialModel)
                .where(
                    ExternalCredentialModel.credential_uuid == credential_uuid,
                    ExternalCredentialModel.organization_id == organization_id,
                )
                .values(**update_values)
            )
            await session.commit()

            # Fetch updated credential
            result = await session.execute(
                select(ExternalCredentialModel).where(
                    ExternalCredentialModel.credential_uuid == credential_uuid
                )
            )
            updated_credential = result.scalar_one()

            logger.info(
                f"Updated webhook credential {credential_uuid} "
                f"for organization {organization_id}"
            )
            return updated_credential

    async def delete_credential(
        self, credential_uuid: str, organization_id: int
    ) -> bool:
        """Soft delete a credential by UUID.

        Args:
            credential_uuid: The unique credential UUID
            organization_id: ID of the organization (for authorization)

        Returns:
            True if credential was deleted, False if not found
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(ExternalCredentialModel)
                .where(
                    ExternalCredentialModel.credential_uuid == credential_uuid,
                    ExternalCredentialModel.organization_id == organization_id,
                    ExternalCredentialModel.is_active.is_(True),
                )
                .values(is_active=False, updated_at=datetime.now(UTC))
            )
            await session.commit()

            if result.rowcount > 0:
                logger.info(
                    f"Soft deleted webhook credential {credential_uuid} "
                    f"for organization {organization_id}"
                )
                return True
            return False

    async def validate_credential_uuid(
        self, credential_uuid: str, organization_id: int
    ) -> bool:
        """Check if a credential UUID exists and belongs to the organization.

        This is useful for workflow validation to ensure referenced credentials exist.

        Args:
            credential_uuid: The credential UUID to validate
            organization_id: ID of the organization

        Returns:
            True if valid, False otherwise
        """
        credential = await self.get_credential_by_uuid(credential_uuid, organization_id)
        return credential is not None
