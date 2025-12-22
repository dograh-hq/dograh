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
