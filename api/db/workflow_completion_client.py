"""Durable ownership and recovery of post-call processing."""

import time

from sqlalchemy import or_, select, text

from api.db.base_client import BaseDBClient
from api.db.models import WorkflowModel, WorkflowRunModel
from api.db.workflow_run_client import append_unique_tags


class WorkflowCompletionClient(BaseDBClient):
    def _completion_run_query(self, run_id: int, organization_id: int):
        return (
            select(WorkflowRunModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                WorkflowRunModel.id == run_id,
                WorkflowModel.organization_id == organization_id,
                WorkflowRunModel.is_completed.is_(True),
            )
            .with_for_update(of=WorkflowRunModel)
        )

    async def claim_workflow_completion(
        self,
        run_id: int,
        organization_id: int,
        token: str,
    ) -> dict | None:
        """Claim unfinished work for longer than the completion job's 300s deadline."""
        async with self.async_session() as session:
            run = await session.scalar(
                self._completion_run_query(run_id, organization_id)
            )
            if run is None:
                return None
            state = (run.extra or {}).get("workflow_completion") or {}
            if not state or state.get("status") == "done":
                return None
            now = time.time()
            if state.get("lease_until", 0) > now:
                return None
            state = {
                **state,
                "status": "running",
                "token": token,
                "lease_until": now + 600,
            }
            run.extra = {**(run.extra or {}), "workflow_completion": state}
            await session.commit()
            return state

    async def checkpoint_workflow_completion(
        self,
        run_id: int,
        organization_id: int,
        token: str,
        step: str,
    ) -> bool:
        async with self.async_session() as session:
            run = await session.scalar(
                self._completion_run_query(run_id, organization_id)
            )
            if run is None:
                return False
            state = (run.extra or {}).get("workflow_completion") or {}
            if (
                state.get("token") != token
                or state.get("lease_until", 0) <= time.time()
            ):
                return False
            state = dict(state)
            if step == "done":
                state.update(status="done", lease_until=0)
            else:
                state["steps"] = append_unique_tags(state.get("steps"), [step])
            run.extra = {**run.extra, "workflow_completion": state}
            await session.commit()
            return True

    async def finish_final_extraction(
        self,
        run_id: int,
        organization_id: int,
        token: str,
        *,
        variables: dict,
        disposition: str | None,
        fallback: str | None,
        mapped_disposition: str | None,
        status: str,
        error_class: str | None,
    ) -> bool:
        """Commit only extraction-owned fields while holding the completion lease."""
        async with self.async_session() as session:
            run = await session.scalar(
                self._completion_run_query(run_id, organization_id)
            )
            if run is None:
                return False
            owner = (run.extra or {}).get("workflow_completion") or {}
            if (
                owner.get("token") != token
                or owner.get("lease_until", 0) <= time.time()
            ):
                return False
            context = dict(run.gathered_context or {})
            state = owner.get("final_extraction") or {}
            if state.get("status") in ("done", "skipped", "failed"):
                return True
            context.update(variables)
            if variables:
                context["extracted_variables"] = {
                    **(context.get("extracted_variables") or {}),
                    **variables,
                }
            tags = []
            if disposition and context.get("call_disposition") == fallback:
                context["call_disposition"] = disposition
                context["mapped_call_disposition"] = mapped_disposition
                tags.append(disposition)
            tags.extend(
                value
                for key, value in context.items()
                if key.startswith("tag_") and isinstance(value, str)
            )
            context["call_tags"] = append_unique_tags(context.get("call_tags"), tags)
            run.extra = {
                **run.extra,
                "workflow_completion": {
                    **owner,
                    "final_extraction": {
                        **state,
                        "status": status,
                        "finished_at": time.time(),
                        "error_class": error_class,
                    },
                },
            }
            run.gathered_context = context
            await session.commit()
            return True

    async def get_overdue_workflow_completions(self, limit: int = 100) -> list[int]:
        """Recover unfinished completions older than 30 minutes with no active lease."""
        now = time.time()
        state = WorkflowRunModel.extra["workflow_completion"]
        async with self.async_session() as session:
            result = await session.scalars(
                select(WorkflowRunModel.id)
                .where(
                    text(
                        "is_completed IS TRUE AND (extra -> 'workflow_completion' ->> 'status') IN ('pending', 'running')"
                    ),
                    state["created_at"].as_float() < now - 30 * 60,
                    or_(
                        state["lease_until"].as_float().is_(None),
                        state["lease_until"].as_float() < now,
                    ),
                )
                .order_by(WorkflowRunModel.id)
                .limit(limit)
            )
            return list(result)
