from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from api.db.base_client import BaseDBClient
from api.db.models import (
    WorkflowModel,
    WorkflowRunModel,
    WorkflowRunTextSessionModel,
)
from api.enums import WorkflowRunMode


class WorkflowRunTextSessionRevisionConflictError(Exception):
    def __init__(self, expected_revision: int, actual_revision: int):
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            "Workflow run text session revision conflict: "
            f"expected {expected_revision}, found {actual_revision}"
        )


class WorkflowRunTextSessionClient(BaseDBClient):
    async def ensure_workflow_run_text_session(
        self,
        workflow_run_id: int,
        session_data: dict | None = None,
        checkpoint: dict | None = None,
    ) -> WorkflowRunTextSessionModel:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunTextSessionModel)
                .where(WorkflowRunTextSessionModel.workflow_run_id == workflow_run_id)
                .with_for_update()
            )
            text_session = result.scalars().first()
            if text_session:
                return text_session

            run_result = await session.execute(
                select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
            )
            workflow_run = run_result.scalars().first()
            if not workflow_run:
                raise ValueError(f"Workflow run with ID {workflow_run_id} not found")

            text_session = WorkflowRunTextSessionModel(
                workflow_run_id=workflow_run_id,
                session_data=session_data or {},
                checkpoint=checkpoint or {},
            )
            session.add(text_session)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(text_session)
        return text_session

    async def get_workflow_run_text_session(
        self,
        workflow_run_id: int,
        *,
        organization_id: int,
    ) -> WorkflowRunTextSessionModel | None:
        async with self.async_session() as session:
            query = (
                select(WorkflowRunTextSessionModel)
                .options(
                    joinedload(WorkflowRunTextSessionModel.workflow_run).joinedload(
                        WorkflowRunModel.workflow
                    )
                )
                .join(WorkflowRunTextSessionModel.workflow_run)
                .join(WorkflowRunModel.workflow)
                .where(WorkflowRunTextSessionModel.workflow_run_id == workflow_run_id)
                .where(WorkflowModel.organization_id == organization_id)
            )

            result = await session.execute(query)
            return result.scalars().first()

    async def get_inactive_workflow_run_text_sessions(
        self,
        *,
        active_since: datetime,
        inactive_before: datetime,
        limit: int = 100,
        after_run_id: int = 0,
    ) -> list[WorkflowRunTextSessionModel]:
        """Return incomplete text chats in the bounded activity window.

        This is intentionally an unscoped background-task query. Each row is
        joined to its persisted workflow run, which is the source of truth for
        organization ownership during downstream completion processing.
        """
        page_size = max(1, min(limit, 500))
        inactive_filter = and_(
            WorkflowRunModel.is_completed.is_(False),
            or_(
                and_(
                    WorkflowRunTextSessionModel.updated_at >= active_since,
                    WorkflowRunTextSessionModel.updated_at <= inactive_before,
                ),
                and_(
                    WorkflowRunTextSessionModel.updated_at.is_(None),
                    WorkflowRunTextSessionModel.created_at >= active_since,
                    WorkflowRunTextSessionModel.created_at <= inactive_before,
                ),
                and_(
                    WorkflowRunTextSessionModel.updated_at.is_(None),
                    WorkflowRunTextSessionModel.created_at.is_(None),
                    WorkflowRunModel.created_at >= active_since,
                    WorkflowRunModel.created_at <= inactive_before,
                ),
            ),
        )
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunTextSessionModel)
                .options(
                    joinedload(WorkflowRunTextSessionModel.workflow_run).joinedload(
                        WorkflowRunModel.definition
                    ),
                    joinedload(WorkflowRunTextSessionModel.workflow_run).joinedload(
                        WorkflowRunModel.workflow
                    ),
                )
                .join(WorkflowRunTextSessionModel.workflow_run)
                .where(WorkflowRunModel.mode == WorkflowRunMode.TEXTCHAT.value)
                .where(inactive_filter)
                .where(WorkflowRunTextSessionModel.workflow_run_id > after_run_id)
                .order_by(WorkflowRunTextSessionModel.workflow_run_id)
                .limit(page_size)
            )
            return list(result.scalars().all())

    async def update_workflow_run_text_session(
        self,
        workflow_run_id: int,
        *,
        session_data: dict | None = None,
        checkpoint: dict | None = None,
        expected_revision: int | None = None,
    ) -> WorkflowRunTextSessionModel:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunTextSessionModel)
                .where(WorkflowRunTextSessionModel.workflow_run_id == workflow_run_id)
                .with_for_update()
            )
            text_session = result.scalars().first()
            if not text_session:
                raise ValueError(
                    f"Workflow run text session with run ID {workflow_run_id} not found"
                )

            if (
                expected_revision is not None
                and text_session.revision != expected_revision
            ):
                raise WorkflowRunTextSessionRevisionConflictError(
                    expected_revision=expected_revision,
                    actual_revision=text_session.revision,
                )

            if session_data is not None:
                text_session.session_data = session_data
            if checkpoint is not None:
                text_session.checkpoint = checkpoint
            text_session.revision += 1

            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e
            await session.refresh(text_session)
        return text_session

    async def complete_workflow_run_text_session(
        self,
        workflow_run_id: int,
        *,
        session_data: dict,
        expected_revision: int | None,
        usage_info: dict,
        gathered_context: dict,
        logs: dict,
        state: str,
        checkpoint: dict | None = None,
        initial_context: dict | None = None,
    ) -> WorkflowRunTextSessionModel:
        """Atomically complete a text session and its owning workflow run."""
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunTextSessionModel)
                .where(WorkflowRunTextSessionModel.workflow_run_id == workflow_run_id)
                .with_for_update()
            )
            text_session = result.scalars().first()
            if not text_session:
                raise ValueError(
                    f"Workflow run text session with run ID {workflow_run_id} not found"
                )
            if (
                expected_revision is not None
                and text_session.revision != expected_revision
            ):
                raise WorkflowRunTextSessionRevisionConflictError(
                    expected_revision=expected_revision,
                    actual_revision=text_session.revision,
                )

            run_result = await session.execute(
                select(WorkflowRunModel)
                .where(WorkflowRunModel.id == workflow_run_id)
                .with_for_update()
            )
            workflow_run = run_result.scalars().first()
            if not workflow_run:
                raise ValueError(f"Workflow run with ID {workflow_run_id} not found")

            text_session.session_data = session_data
            if checkpoint is not None:
                text_session.checkpoint = checkpoint
            text_session.revision += 1

            workflow_run.usage_info = usage_info
            if initial_context:
                workflow_run.initial_context = {
                    **(workflow_run.initial_context or {}),
                    **initial_context,
                }
            workflow_run.gathered_context = {
                **(workflow_run.gathered_context or {}),
                **gathered_context,
            }
            workflow_run.logs = {
                **(workflow_run.logs or {}),
                **logs,
            }
            workflow_run.state = state
            workflow_run.is_completed = True

            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            await session.refresh(text_session)
            return text_session
