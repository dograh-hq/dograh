"""Database client for managing agent triggers."""

from typing import List, Optional

from loguru import logger
from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert

from api.db.base_client import BaseDBClient
from api.db.models import AgentTriggerModel
from api.enums import TriggerState


class AgentTriggerClient(BaseDBClient):
    """Client for managing agent triggers (UUID -> workflow_id mappings)."""

    async def get_agent_trigger_by_path(
        self, trigger_path: str, active_only: bool = True
    ) -> Optional[AgentTriggerModel]:
        """Get an agent trigger by its unique path (UUID).

        Args:
            trigger_path: The unique trigger UUID
            active_only: If True, only return active triggers

        Returns:
            AgentTriggerModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = select(AgentTriggerModel).where(
                AgentTriggerModel.trigger_path == trigger_path
            )

            if active_only:
                query = query.where(
                    AgentTriggerModel.state == TriggerState.ACTIVE.value
                )

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_triggers_for_workflow(
        self, workflow_id: int, active_only: bool = True
    ) -> List[AgentTriggerModel]:
        """Get all triggers for a specific workflow.

        Args:
            workflow_id: ID of the workflow
            active_only: If True, only return active triggers

        Returns:
            List of AgentTriggerModel instances
        """
        async with self.async_session() as session:
            query = select(AgentTriggerModel).where(
                AgentTriggerModel.workflow_id == workflow_id
            )

            if active_only:
                query = query.where(
                    AgentTriggerModel.state == TriggerState.ACTIVE.value
                )

            result = await session.execute(query)
            return list(result.scalars().all())

    async def upsert_agent_trigger(
        self, trigger_path: str, workflow_id: int, organization_id: int
    ) -> AgentTriggerModel:
        """Create or update an agent trigger.

        Uses PostgreSQL upsert to handle the case where the trigger already exists.
        If the trigger was archived, it will be reactivated.

        Args:
            trigger_path: The unique trigger UUID
            workflow_id: ID of the workflow
            organization_id: ID of the organization

        Returns:
            The created or updated AgentTriggerModel
        """
        async with self.async_session() as session:
            # Use PostgreSQL INSERT ... ON CONFLICT DO UPDATE
            stmt = insert(AgentTriggerModel).values(
                trigger_path=trigger_path,
                workflow_id=workflow_id,
                organization_id=organization_id,
                state=TriggerState.ACTIVE.value,
            )

            # On conflict (trigger_path already exists), update and reactivate
            stmt = stmt.on_conflict_do_update(
                index_elements=["trigger_path"],
                set_={
                    "workflow_id": workflow_id,
                    "organization_id": organization_id,
                    "state": TriggerState.ACTIVE.value,
                },
            )

            await session.execute(stmt)
            await session.commit()

            # Fetch the trigger to return it
            result = await session.execute(
                select(AgentTriggerModel).where(
                    AgentTriggerModel.trigger_path == trigger_path
                )
            )
            trigger = result.scalar_one()

            logger.info(
                f"Upserted agent trigger {trigger_path} for workflow {workflow_id}"
            )
            return trigger

    async def sync_triggers_for_workflow(
        self, workflow_id: int, organization_id: int, trigger_paths: List[str]
    ) -> None:
        """Sync triggers for a workflow based on the trigger nodes in the workflow definition.

        This creates/reactivates triggers that are in the workflow definition
        and archives triggers that are no longer in the workflow.

        Args:
            workflow_id: ID of the workflow
            organization_id: ID of the organization
            trigger_paths: List of trigger UUIDs from the workflow definition
        """
        async with self.async_session() as session:
            # Get all existing triggers for this workflow (including archived)
            result = await session.execute(
                select(AgentTriggerModel).where(
                    AgentTriggerModel.workflow_id == workflow_id
                )
            )
            existing_triggers = {t.trigger_path: t for t in result.scalars().all()}

            existing_paths = set(existing_triggers.keys())
            new_paths = set(trigger_paths)

            # Archive triggers that are no longer in the workflow definition
            paths_to_archive = existing_paths - new_paths
            if paths_to_archive:
                await session.execute(
                    update(AgentTriggerModel)
                    .where(AgentTriggerModel.trigger_path.in_(paths_to_archive))
                    .values(state=TriggerState.ARCHIVED.value)
                )
                logger.info(
                    f"Archived {len(paths_to_archive)} triggers for workflow {workflow_id}"
                )

            # Reactivate existing triggers that are back in the workflow
            paths_to_reactivate = new_paths & existing_paths
            if paths_to_reactivate:
                await session.execute(
                    update(AgentTriggerModel)
                    .where(
                        and_(
                            AgentTriggerModel.trigger_path.in_(paths_to_reactivate),
                            AgentTriggerModel.state == TriggerState.ARCHIVED.value,
                        )
                    )
                    .values(state=TriggerState.ACTIVE.value)
                )

            # Add new triggers
            paths_to_add = new_paths - existing_paths
            for trigger_path in paths_to_add:
                stmt = insert(AgentTriggerModel).values(
                    trigger_path=trigger_path,
                    workflow_id=workflow_id,
                    organization_id=organization_id,
                    state=TriggerState.ACTIVE.value,
                )
                # Handle race condition where trigger might already exist for another workflow
                stmt = stmt.on_conflict_do_update(
                    index_elements=["trigger_path"],
                    set_={
                        "workflow_id": workflow_id,
                        "organization_id": organization_id,
                        "state": TriggerState.ACTIVE.value,
                    },
                )
                await session.execute(stmt)

            if paths_to_add:
                logger.info(
                    f"Added {len(paths_to_add)} triggers for workflow {workflow_id}"
                )

            await session.commit()

    async def archive_triggers_for_workflow(self, workflow_id: int) -> int:
        """Archive all triggers for a workflow (soft delete).

        Args:
            workflow_id: ID of the workflow

        Returns:
            Number of triggers archived
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(AgentTriggerModel)
                .where(
                    and_(
                        AgentTriggerModel.workflow_id == workflow_id,
                        AgentTriggerModel.state == TriggerState.ACTIVE.value,
                    )
                )
                .values(state=TriggerState.ARCHIVED.value)
            )
            await session.commit()

            archived_count = result.rowcount
            if archived_count > 0:
                logger.info(
                    f"Archived {archived_count} triggers for workflow {workflow_id}"
                )
            return archived_count

    async def archive_trigger_by_path(self, trigger_path: str) -> bool:
        """Archive a specific trigger by its path (soft delete).

        Args:
            trigger_path: The unique trigger UUID

        Returns:
            True if trigger was archived, False if not found
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(AgentTriggerModel)
                .where(
                    and_(
                        AgentTriggerModel.trigger_path == trigger_path,
                        AgentTriggerModel.state == TriggerState.ACTIVE.value,
                    )
                )
                .values(state=TriggerState.ARCHIVED.value)
            )
            await session.commit()
            return result.rowcount > 0
