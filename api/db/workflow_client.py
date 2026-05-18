from datetime import UTC, datetime
from typing import Optional

from loguru import logger
from sqlalchemy import func, update
from sqlalchemy.future import select
from sqlalchemy.orm import load_only, selectinload

from api.db.base_client import BaseDBClient
from api.db.models import WorkflowDefinitionModel, WorkflowModel, WorkflowRunModel


class WorkflowClient(BaseDBClient):
    async def _next_version_number(self, session, workflow_id: int) -> int:
        """Get the next version number for a workflow."""
        result = await session.execute(
            select(func.max(WorkflowDefinitionModel.version_number)).where(
                WorkflowDefinitionModel.workflow_id == workflow_id,
            )
        )
        current_max = result.scalar()
        return (current_max or 0) + 1

    async def create_workflow(
        self,
        name: str,
        workflow_definition: dict,
        user_id: int,
        organization_id: int = None,
    ) -> WorkflowModel:
        async with self.async_session() as session:
            try:
                new_workflow = WorkflowModel(
                    name=name,
                    workflow_definition=workflow_definition,  # Keep for backwards compatibility
                    user_id=user_id,
                    organization_id=organization_id,
                )
                session.add(new_workflow)
                await session.flush()  # Flush to get the workflow ID

                # Create the first definition as V1 published
                definition = WorkflowDefinitionModel(
                    workflow_json=workflow_definition,
                    workflow_id=new_workflow.id,
                    is_current=True,
                    status="published",
                    version_number=1,
                    published_at=datetime.now(UTC),
                    workflow_configurations=new_workflow.workflow_configurations or {},
                    template_context_variables=new_workflow.template_context_variables
                    or {},
                )
                session.add(definition)
                await session.flush()

                # Set the released pointer
                new_workflow.released_definition_id = definition.id

                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(new_workflow)
        return new_workflow

    # ------------------------------------------------------------------
    # Versioning methods
    # ------------------------------------------------------------------

    async def save_workflow_draft(
        self,
        workflow_id: int,
        workflow_definition: dict | None = None,
        workflow_configurations: dict | None = None,
        template_context_variables: dict | None = None,
    ) -> WorkflowDefinitionModel:
        """Create or update a draft version for this workflow.

        If a draft already exists, it is updated in place.
        If no draft exists, a new one is created with the next version number.
        """
        async with self.async_session() as session:
            # Check for existing draft
            result = await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status == "draft",
                )
            )
            draft = result.scalars().first()

            if draft:
                # Update existing draft in place
                if workflow_definition is not None:
                    draft.workflow_json = workflow_definition
                if workflow_configurations is not None:
                    draft.workflow_configurations = workflow_configurations
                if template_context_variables is not None:
                    draft.template_context_variables = template_context_variables
            else:
                # Get current published to use as base for unspecified fields
                pub_result = await session.execute(
                    select(WorkflowDefinitionModel).where(
                        WorkflowDefinitionModel.workflow_id == workflow_id,
                        WorkflowDefinitionModel.status == "published",
                    )
                )
                published = pub_result.scalars().first()

                next_version = await self._next_version_number(session, workflow_id)

                draft = WorkflowDefinitionModel(
                    workflow_id=workflow_id,
                    workflow_json=workflow_definition
                    if workflow_definition is not None
                    else (published.workflow_json if published else {}),
                    workflow_configurations=workflow_configurations
                    if workflow_configurations is not None
                    else (published.workflow_configurations if published else {}),
                    template_context_variables=template_context_variables
                    if template_context_variables is not None
                    else (published.template_context_variables if published else {}),
                    status="draft",
                    version_number=next_version,
                    is_current=False,
                )
                session.add(draft)

            # Keep legacy columns on workflows table in sync with draft
            wf_result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            workflow = wf_result.scalars().first()
            if workflow:
                workflow.workflow_definition = draft.workflow_json
                workflow.workflow_configurations = draft.workflow_configurations
                workflow.template_context_variables = draft.template_context_variables

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(draft)
        return draft

    async def publish_workflow_draft(
        self,
        workflow_id: int,
    ) -> WorkflowDefinitionModel:
        """Promote the current draft to published.

        - Draft → published
        - Previous published → archived
        - Updates released_definition_id on the workflow
        - Sets is_current for backward compatibility
        """
        async with self.async_session() as session:
            # Find the draft
            result = await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status == "draft",
                )
            )
            draft = result.scalars().first()
            if not draft:
                raise ValueError(f"No draft exists for workflow {workflow_id}")

            # Archive the current published version
            await session.execute(
                update(WorkflowDefinitionModel)
                .where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status == "published",
                )
                .values(status="archived", is_current=False)
            )

            # Promote draft → published
            draft.status = "published"
            draft.published_at = datetime.now(UTC)
            draft.is_current = True

            # Update workflow's released pointer + legacy fields
            wf_result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            workflow = wf_result.scalars().first()
            workflow.released_definition_id = draft.id
            workflow.workflow_definition = draft.workflow_json
            workflow.workflow_configurations = draft.workflow_configurations
            workflow.template_context_variables = draft.template_context_variables

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(draft)
        return draft

    async def discard_workflow_draft(
        self,
        workflow_id: int,
    ) -> None:
        """Delete the current draft version."""
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status == "draft",
                )
            )
            draft = result.scalars().first()
            if not draft:
                raise ValueError(f"No draft exists for workflow {workflow_id}")

            await session.delete(draft)

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e

    async def revert_to_version(
        self,
        workflow_id: int,
        definition_id: int,
    ) -> WorkflowDefinitionModel:
        """Create a new draft from an archived version's snapshot.

        Raises ValueError if a draft already exists (must discard first).
        """
        async with self.async_session() as session:
            # Ensure no existing draft
            draft_result = await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status == "draft",
                )
            )
            if draft_result.scalars().first():
                raise ValueError(
                    f"Draft already exists for workflow {workflow_id}. "
                    "Discard it before reverting."
                )

            # Fetch the source version
            source_result = await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.id == definition_id,
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                )
            )
            source = source_result.scalars().first()
            if not source:
                raise ValueError(
                    f"Version {definition_id} not found for workflow {workflow_id}"
                )

            next_version = await self._next_version_number(session, workflow_id)

            # Create new draft from the source snapshot
            draft = WorkflowDefinitionModel(
                workflow_id=workflow_id,
                workflow_json=source.workflow_json,
                workflow_configurations=source.workflow_configurations,
                template_context_variables=source.template_context_variables,
                status="draft",
                version_number=next_version,
                is_current=False,
            )
            session.add(draft)

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(draft)
        return draft

    async def get_draft_version(
        self,
        workflow_id: int,
    ) -> WorkflowDefinitionModel | None:
        """Get the draft version for a workflow, or None if no draft exists."""
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status == "draft",
                )
            )
            return result.scalars().first()

    async def get_workflow_versions(
        self,
        workflow_id: int,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowDefinitionModel]:
        """List versions for a workflow, newest first.

        When `limit` is provided, returns at most `limit` rows starting from
        `offset` — used by the version history panel to page through long
        histories without dragging the full `workflow_json` payload for every
        version on every open.
        """
        async with self.async_session() as session:
            query = (
                select(WorkflowDefinitionModel)
                .where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status.in_(
                        ["published", "draft", "archived"]
                    ),
                )
                .order_by(WorkflowDefinitionModel.version_number.desc())
            )
            if offset:
                query = query.offset(offset)
            if limit is not None:
                query = query.limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def get_all_workflows(
        self, user_id: int = None, organization_id: int = None, status: str = None
    ) -> list[WorkflowModel]:
        async with self.async_session() as session:
            query = select(WorkflowModel).options(
                selectinload(WorkflowModel.current_definition)
            )

            if organization_id:
                # Filter by organization_id when provided
                query = query.where(WorkflowModel.organization_id == organization_id)
            elif user_id:
                # Fallback to user_id for backwards compatibility
                query = query.where(WorkflowModel.user_id == user_id)

            # Filter by status if provided
            if status:
                query = query.where(WorkflowModel.status == status)

            result = await session.execute(query)
            return result.scalars().all()

    async def get_all_workflows_for_listing(
        self, organization_id: int = None, status: str = None
    ) -> list[WorkflowModel]:
        """Get workflows with only the columns needed for listing.

        This is an optimized version that excludes large JSON columns like
        workflow_definition, template_context_variables, etc.

        Args:
            organization_id: Filter by organization ID
            status: Filter by status (active/archived)

        Returns:
            List of WorkflowModel with only id, name, status, created_at loaded
        """
        async with self.async_session() as session:
            query = select(WorkflowModel).options(
                load_only(
                    WorkflowModel.id,
                    WorkflowModel.name,
                    WorkflowModel.status,
                    WorkflowModel.created_at,
                )
            )

            if organization_id:
                query = query.where(WorkflowModel.organization_id == organization_id)

            if status:
                query = query.where(WorkflowModel.status == status)

            result = await session.execute(query)
            return result.scalars().all()

    async def get_workflow_counts(self, organization_id: int = None) -> dict[str, int]:
        """Get workflow counts by status.

        Args:
            organization_id: Filter by organization ID

        Returns:
            Dict with 'total', 'active', 'archived' counts
        """
        async with self.async_session() as session:
            query = select(
                WorkflowModel.status,
                func.count(WorkflowModel.id).label("count"),
            )

            if organization_id:
                query = query.where(WorkflowModel.organization_id == organization_id)

            query = query.group_by(WorkflowModel.status)

            result = await session.execute(query)
            rows = result.all()

            counts = {"total": 0, "active": 0, "archived": 0}
            for status, count in rows:
                counts[status] = count
                counts["total"] += count

            return counts

    async def get_workflow_organization_id(self, workflow_id: int) -> int | None:
        """Fetch only the organization_id for a workflow. Lightweight query."""
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowModel.organization_id).where(
                    WorkflowModel.id == workflow_id
                )
            )
            return result.scalar_one_or_none()

    async def get_workflow(
        self, workflow_id: int, user_id: int = None, organization_id: int = None
    ) -> WorkflowModel | None:
        async with self.async_session() as session:
            query = (
                select(WorkflowModel)
                .options(
                    selectinload(WorkflowModel.current_definition),
                    selectinload(WorkflowModel.released_definition),
                )
                .where(WorkflowModel.id == workflow_id)
            )

            if organization_id:
                # Filter by organization_id when provided
                query = query.where(WorkflowModel.organization_id == organization_id)
            elif user_id:
                # Fallback to user_id for backwards compatibility
                query = query.where(WorkflowModel.user_id == user_id)

            result = await session.execute(query)
            return result.scalars().first()

    async def get_workflow_by_id(self, workflow_id: int) -> WorkflowModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowModel)
                .options(
                    selectinload(WorkflowModel.current_definition),
                    selectinload(WorkflowModel.released_definition),
                )
                .where(WorkflowModel.id == workflow_id)
            )
            return result.scalars().first()

    async def get_workflow_by_uuid(
        self, workflow_uuid: str, organization_id: int
    ) -> WorkflowModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowModel)
                .options(
                    selectinload(WorkflowModel.current_definition),
                    selectinload(WorkflowModel.released_definition),
                )
                .where(
                    WorkflowModel.workflow_uuid == workflow_uuid,
                    WorkflowModel.organization_id == organization_id,
                )
            )
            return result.scalars().first()

    async def get_workflow_by_uuid_unscoped(
        self, workflow_uuid: str
    ) -> WorkflowModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowModel)
                .options(
                    selectinload(WorkflowModel.current_definition),
                    selectinload(WorkflowModel.released_definition),
                )
                .where(WorkflowModel.workflow_uuid == workflow_uuid)
            )
            return result.scalars().first()

    async def update_workflow(
        self,
        workflow_id: int,
        name: str | None,
        workflow_definition: dict | None,
        template_context_variables: dict | None,
        workflow_configurations: dict | None,
        user_id: int = None,
        organization_id: int = None,
    ) -> WorkflowModel:
        """
        Update an existing workflow in the database.

        Name changes are applied directly to the workflow.
        Definition/config/template_var changes are saved as a draft version
        via save_workflow_draft, keeping the published version unchanged.

        Args:
            workflow_id: The ID of the workflow to update
            name: The new name for the workflow
            workflow_definition: The new workflow definition
            template_context_variables: The template context variables
            workflow_configurations: The workflow configurations
            user_id: The user ID (for backwards compatibility)
            organization_id: The organization ID

        Returns:
            The updated WorkflowModel

        Raises:
            ValueError: If the workflow with the given ID is not found
        """
        async with self.async_session() as session:
            query = (
                select(WorkflowModel)
                .options(selectinload(WorkflowModel.current_definition))
                .where(WorkflowModel.id == workflow_id)
            )

            if organization_id:
                query = query.where(WorkflowModel.organization_id == organization_id)
            elif user_id:
                query = query.where(WorkflowModel.user_id == user_id)

            result = await session.execute(query)
            workflow = result.scalars().first()
            if not workflow:
                raise ValueError(f"Workflow with ID {workflow_id} not found")

            # Name is a workflow-level field, not versioned
            if name is not None:
                workflow.name = name

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(workflow)

        # Save versioned changes as a draft
        has_versioned_changes = any(
            v is not None
            for v in [
                workflow_definition,
                workflow_configurations,
                template_context_variables,
            ]
        )
        if has_versioned_changes:
            await self.save_workflow_draft(
                workflow_id=workflow_id,
                workflow_definition=workflow_definition,
                workflow_configurations=workflow_configurations,
                template_context_variables=template_context_variables,
            )
            # Re-fetch with updated state
            workflow = await self.get_workflow(
                workflow_id, user_id=user_id, organization_id=organization_id
            )

        return workflow

    async def get_workflows_by_ids(
        self, workflow_ids: list[int], organization_id: int
    ) -> list[WorkflowModel]:
        """Get workflows by IDs for a specific organization"""
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowModel)
                .join(WorkflowModel.user)
                .where(
                    WorkflowModel.id.in_(workflow_ids),
                    WorkflowModel.user.has(selected_organization_id=organization_id),
                )
            )
            return result.scalars().all()

    async def get_workflow_name(
        self, workflow_id: int, user_id: int = None, organization_id: int = None
    ) -> Optional[str]:
        """Get just the workflow name by ID"""
        async with self.async_session() as session:
            query = select(WorkflowModel.name).where(WorkflowModel.id == workflow_id)

            if organization_id:
                # Filter by organization_id when provided
                query = query.where(WorkflowModel.organization_id == organization_id)
            elif user_id:
                # Fallback to user_id for backwards compatibility
                query = query.where(WorkflowModel.user_id == user_id)

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def update_workflow_status(
        self,
        workflow_id: int,
        status: str,
        organization_id: int = None,
    ) -> WorkflowModel:
        """
        Update the status of a workflow.

        Args:
            workflow_id: The ID of the workflow to update
            status: The new status (active/archived)
            organization_id: The organization ID

        Returns:
            The updated WorkflowModel

        Raises:
            ValueError: If the workflow is not found
        """
        async with self.async_session() as session:
            query = (
                select(WorkflowModel)
                .options(
                    selectinload(WorkflowModel.current_definition),
                    selectinload(WorkflowModel.released_definition),
                )
                .where(WorkflowModel.id == workflow_id)
            )

            if organization_id:
                query = query.where(WorkflowModel.organization_id == organization_id)

            result = await session.execute(query)
            workflow = result.scalars().first()

            if not workflow:
                raise ValueError(f"Workflow with ID {workflow_id} not found")

            workflow.status = status

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(workflow)
        return workflow

    async def get_workflow_run_count(self, workflow_id: int) -> int:
        """Get the count of runs for a workflow."""
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(WorkflowRunModel.id)).where(
                    WorkflowRunModel.workflow_id == workflow_id
                )
            )
            return result.scalar() or 0

    async def update_definition_node_summaries(
        self, definition_id: int, node_summaries: dict
    ) -> None:
        """Update the node_summaries field within a workflow definition's workflow_json.

        Args:
            definition_id: The ID of the WorkflowDefinitionModel to update
            node_summaries: Dict mapping node_id to summary data
                (e.g. {"summary": "...", "trace_url": "..."})
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.id == definition_id
                )
            )
            definition = result.scalars().first()
            if not definition:
                return

            workflow_json = dict(definition.workflow_json)
            workflow_json["node_summaries"] = node_summaries
            definition.workflow_json = workflow_json

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e

    async def get_workflow_run_counts(self, workflow_ids: list[int]) -> dict[int, int]:
        """Get run counts for multiple workflows in a single query.

        Args:
            workflow_ids: List of workflow IDs to get counts for

        Returns:
            Dict mapping workflow_id to run count
        """
        if not workflow_ids:
            return {}

        async with self.async_session() as session:
            result = await session.execute(
                select(
                    WorkflowRunModel.workflow_id,
                    func.count(WorkflowRunModel.id).label("run_count"),
                )
                .where(WorkflowRunModel.workflow_id.in_(workflow_ids))
                .group_by(WorkflowRunModel.workflow_id)
            )
            rows = result.all()

            # Build dict with counts, defaulting to 0 for workflows with no runs
            counts = {workflow_id: 0 for workflow_id in workflow_ids}
            for workflow_id, run_count in rows:
                counts[workflow_id] = run_count

            return counts

    async def add_call_disposition_code(
        self, workflow_id: int, disposition_code: str
    ) -> None:
        """Add a disposition code to the workflow's call_disposition_codes if not already present.

        The codes are stored as {"disposition_codes": ["code1", "code2", ...]}.
        """
        if not disposition_code:
            return

        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowModel).where(WorkflowModel.id == workflow_id)
            )
            workflow = result.scalars().first()
            if not workflow:
                return

            existing = workflow.call_disposition_codes or {}
            codes = list(existing.get("disposition_codes", []))
            if disposition_code in codes:
                return

            codes.append(disposition_code)
            workflow.call_disposition_codes = {"disposition_codes": codes}

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"Failed to add disposition code '{disposition_code}' "
                    f"to workflow {workflow_id}: {e}"
                )
