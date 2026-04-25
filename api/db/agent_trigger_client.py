"""Database client for managing agent triggers."""

from typing import List, Optional

from loguru import logger
from sqlalchemy import and_, insert, select, update

from api.db.base_client import BaseDBClient
from api.db.models import AgentTriggerModel
from api.enums import TriggerState


class TriggerPathConflictError(Exception):
    """Raised when a trigger path is already in use by a different workflow.

    ``trigger_path`` is globally unique, so any conflict — same org or
    cross-org — surfaces here.
    """

    def __init__(self, trigger_paths: List[str]):
        self.trigger_paths = list(trigger_paths)
        joined = ", ".join(self.trigger_paths)
        super().__init__(f"Trigger path(s) already in use by another agent: {joined}")


class AgentTriggerClient(BaseDBClient):
    """Client for managing agent triggers (UUID -> workflow_id mappings)."""

    async def get_agent_trigger_by_path(
        self, trigger_path: str, active_only: bool = True
    ) -> Optional[AgentTriggerModel]:
        """Get an agent trigger by its globally unique path (UUID).

        Args:
            trigger_path: The trigger UUID
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

    async def check_trigger_path_conflicts(
        self,
        trigger_paths: List[str],
        exclude_workflow_id: Optional[int] = None,
    ) -> List[str]:
        """Return any trigger paths already in use by a different workflow.

        Archived triggers count as conflicts — we never silently repurpose
        another workflow's trigger.

        Args:
            trigger_paths: Paths to check
            exclude_workflow_id: Workflow that may legitimately own these paths
                (used during updates to ignore the workflow's own triggers)

        Returns:
            List of conflicting trigger paths (empty if no conflicts).
        """
        if not trigger_paths:
            return []

        async with self.async_session() as session:
            query = select(AgentTriggerModel.trigger_path).where(
                AgentTriggerModel.trigger_path.in_(trigger_paths),
            )
            if exclude_workflow_id is not None:
                query = query.where(
                    AgentTriggerModel.workflow_id != exclude_workflow_id
                )
            result = await session.execute(query)
            return [row[0] for row in result.all()]

    async def assert_trigger_paths_available(
        self,
        trigger_paths: List[str],
        exclude_workflow_id: Optional[int] = None,
    ) -> None:
        """Raise TriggerPathConflictError if any path is already in use."""
        conflicts = await self.check_trigger_path_conflicts(
            trigger_paths=trigger_paths,
            exclude_workflow_id=exclude_workflow_id,
        )
        if conflicts:
            raise TriggerPathConflictError(conflicts)

    async def sync_triggers_for_workflow(
        self, workflow_id: int, organization_id: int, trigger_paths: List[str]
    ) -> None:
        """Sync triggers for a workflow based on the trigger nodes in the workflow definition.

        Creates/reactivates triggers that are in the workflow definition and
        archives triggers that are no longer in the workflow.

        Raises TriggerPathConflictError if any new trigger path is already in
        use by another workflow. Callers should invoke
        ``assert_trigger_paths_available`` upfront so the workflow is not
        created/updated when a conflict will block trigger sync.

        Args:
            workflow_id: ID of the workflow
            organization_id: ID of the organization
            trigger_paths: List of trigger UUIDs from the workflow definition
        """
        async with self.async_session() as session:
            # Existing triggers tied to THIS workflow (any state)
            result = await session.execute(
                select(AgentTriggerModel).where(
                    AgentTriggerModel.workflow_id == workflow_id
                )
            )
            existing_triggers = {t.trigger_path: t for t in result.scalars().all()}

            existing_paths = set(existing_triggers.keys())
            new_paths = set(trigger_paths)
            paths_to_add = new_paths - existing_paths

            # Refuse to take over a trigger owned by another workflow
            # (active or archived). The global unique constraint on
            # trigger_path backstops races between this check and the
            # insert below.
            if paths_to_add:
                conflict_result = await session.execute(
                    select(AgentTriggerModel.trigger_path).where(
                        AgentTriggerModel.trigger_path.in_(paths_to_add),
                        AgentTriggerModel.workflow_id != workflow_id,
                    )
                )
                conflicts = [row[0] for row in conflict_result.all()]
                if conflicts:
                    raise TriggerPathConflictError(conflicts)

            # Archive triggers that are no longer in the workflow definition
            paths_to_archive = existing_paths - new_paths
            if paths_to_archive:
                await session.execute(
                    update(AgentTriggerModel)
                    .where(
                        and_(
                            AgentTriggerModel.workflow_id == workflow_id,
                            AgentTriggerModel.trigger_path.in_(paths_to_archive),
                        )
                    )
                    .values(state=TriggerState.ARCHIVED.value)
                )
                logger.info(
                    f"Archived {len(paths_to_archive)} triggers for workflow {workflow_id}"
                )

            # Reactivate this workflow's previously-archived triggers
            paths_to_reactivate = new_paths & existing_paths
            if paths_to_reactivate:
                await session.execute(
                    update(AgentTriggerModel)
                    .where(
                        and_(
                            AgentTriggerModel.workflow_id == workflow_id,
                            AgentTriggerModel.trigger_path.in_(paths_to_reactivate),
                            AgentTriggerModel.state == TriggerState.ARCHIVED.value,
                        )
                    )
                    .values(state=TriggerState.ACTIVE.value)
                )

            for trigger_path in paths_to_add:
                await session.execute(
                    insert(AgentTriggerModel).values(
                        trigger_path=trigger_path,
                        workflow_id=workflow_id,
                        organization_id=organization_id,
                        state=TriggerState.ACTIVE.value,
                    )
                )

            if paths_to_add:
                logger.info(
                    f"Added {len(paths_to_add)} triggers for workflow {workflow_id}"
                )

            await session.commit()
