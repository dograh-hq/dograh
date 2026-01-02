"""Database client for managing tools."""

from datetime import UTC, datetime
from typing import List, Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from api.db.base_client import BaseDBClient
from api.db.models import ToolModel
from api.enums import ToolCategory, ToolStatus


class ToolClient(BaseDBClient):
    """Client for managing tools (organization-scoped, UUID-referenced)."""

    async def create_tool(
        self,
        organization_id: int,
        user_id: int,
        name: str,
        definition: dict,
        category: str = ToolCategory.HTTP_API.value,
        description: Optional[str] = None,
        icon: Optional[str] = None,
        icon_color: Optional[str] = None,
    ) -> ToolModel:
        """Create a new tool.

        Args:
            organization_id: ID of the organization
            user_id: ID of the user creating the tool
            name: Display name for the tool
            definition: JSON definition of the tool
            category: Tool category (http_api, native, integration)
            description: Optional description
            icon: Optional icon identifier
            icon_color: Optional hex color code

        Returns:
            The created ToolModel with auto-generated UUID
        """
        async with self.async_session() as session:
            tool = ToolModel(
                organization_id=organization_id,
                created_by=user_id,
                name=name,
                description=description,
                category=category,
                icon=icon,
                icon_color=icon_color,
                definition=definition,
                status=ToolStatus.ACTIVE.value,
            )

            session.add(tool)
            await session.commit()
            await session.refresh(tool)

            logger.info(
                f"Created tool '{name}' ({tool.tool_uuid}) "
                f"for organization {organization_id}"
            )
            return tool

    async def get_tools_for_organization(
        self,
        organization_id: int,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[ToolModel]:
        """Get all tools for an organization.

        Args:
            organization_id: ID of the organization
            status: Optional filter by status (active, archived, draft)
            category: Optional filter by category (http_api, native, integration)

        Returns:
            List of ToolModel instances
        """
        async with self.async_session() as session:
            query = select(ToolModel).where(
                ToolModel.organization_id == organization_id
            )

            if status:
                query = query.where(ToolModel.status == status)
            else:
                # By default, exclude archived tools
                query = query.where(ToolModel.status != ToolStatus.ARCHIVED.value)

            if category:
                query = query.where(ToolModel.category == category)

            query = query.order_by(ToolModel.name)

            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_tool_by_uuid(
        self,
        tool_uuid: str,
        organization_id: int,
        include_archived: bool = False,
    ) -> Optional[ToolModel]:
        """Get a tool by its UUID, scoped to organization.

        Args:
            tool_uuid: The unique tool UUID
            organization_id: ID of the organization (for authorization)
            include_archived: If True, include archived tools

        Returns:
            ToolModel if found and authorized, None otherwise
        """
        async with self.async_session() as session:
            query = (
                select(ToolModel)
                .where(
                    ToolModel.tool_uuid == tool_uuid,
                    ToolModel.organization_id == organization_id,
                )
                .options(selectinload(ToolModel.created_by_user))
            )

            if not include_archived:
                query = query.where(ToolModel.status != ToolStatus.ARCHIVED.value)

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def update_tool(
        self,
        tool_uuid: str,
        organization_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        definition: Optional[dict] = None,
        icon: Optional[str] = None,
        icon_color: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[ToolModel]:
        """Update a tool by UUID.

        Args:
            tool_uuid: The unique tool UUID
            organization_id: ID of the organization (for authorization)
            name: New name (if provided)
            description: New description (if provided)
            definition: New definition (if provided)
            icon: New icon (if provided)
            icon_color: New icon color (if provided)
            status: New status (if provided)

        Returns:
            Updated ToolModel if found, None otherwise
        """
        async with self.async_session() as session:
            # First check if tool exists and belongs to organization
            tool = await self.get_tool_by_uuid(
                tool_uuid, organization_id, include_archived=True
            )
            if not tool:
                return None

            # Build update values
            update_values = {"updated_at": datetime.now(UTC)}
            if name is not None:
                update_values["name"] = name
            if description is not None:
                update_values["description"] = description
            if definition is not None:
                update_values["definition"] = definition
            if icon is not None:
                update_values["icon"] = icon
            if icon_color is not None:
                update_values["icon_color"] = icon_color
            if status is not None:
                update_values["status"] = status

            await session.execute(
                update(ToolModel)
                .where(
                    ToolModel.tool_uuid == tool_uuid,
                    ToolModel.organization_id == organization_id,
                )
                .values(**update_values)
            )
            await session.commit()

            # Fetch updated tool
            result = await session.execute(
                select(ToolModel)
                .where(ToolModel.tool_uuid == tool_uuid)
                .options(selectinload(ToolModel.created_by_user))
            )
            updated_tool = result.scalar_one()

            logger.info(f"Updated tool {tool_uuid} for organization {organization_id}")
            return updated_tool

    async def archive_tool(self, tool_uuid: str, organization_id: int) -> bool:
        """Soft delete a tool by setting its status to archived.

        Args:
            tool_uuid: The unique tool UUID
            organization_id: ID of the organization (for authorization)

        Returns:
            True if tool was archived, False if not found
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(ToolModel)
                .where(
                    ToolModel.tool_uuid == tool_uuid,
                    ToolModel.organization_id == organization_id,
                    ToolModel.status != ToolStatus.ARCHIVED.value,
                )
                .values(
                    status=ToolStatus.ARCHIVED.value,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

            if result.rowcount > 0:
                logger.info(
                    f"Archived tool {tool_uuid} for organization {organization_id}"
                )
                return True
            return False

    async def validate_tool_uuid(self, tool_uuid: str, organization_id: int) -> bool:
        """Check if a tool UUID exists and belongs to the organization.

        This is useful for workflow validation to ensure referenced tools exist.

        Args:
            tool_uuid: The tool UUID to validate
            organization_id: ID of the organization

        Returns:
            True if valid, False otherwise
        """
        tool = await self.get_tool_by_uuid(tool_uuid, organization_id)
        return tool is not None

    async def get_tools_by_uuids(
        self,
        tool_uuids: List[str],
        organization_id: int,
    ) -> List[ToolModel]:
        """Get multiple tools by their UUIDs.

        Args:
            tool_uuids: List of tool UUIDs to fetch
            organization_id: ID of the organization (for authorization)

        Returns:
            List of ToolModel instances (only active tools)
        """
        if not tool_uuids:
            return []

        async with self.async_session() as session:
            query = select(ToolModel).where(
                ToolModel.tool_uuid.in_(tool_uuids),
                ToolModel.organization_id == organization_id,
                ToolModel.status == ToolStatus.ACTIVE.value,
            )

            result = await session.execute(query)
            return list(result.scalars().all())
