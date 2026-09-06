import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, cast, delete, func, or_
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    CallScoreModel,
    SakinahRunModel,
    SakinahScenarioModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)

SCENARIO_FIELDS = (
    "title",
    "category",
    "tags",
    "mode",
    "persona",
    "age",
    "gender",
    "language",
    "emotion",
    "communication_style",
    "initial_information",
    "hidden_information",
    "disclosure",
    "behaviour",
    "background",
    "additional_factors",
    "notes",
    "freestyle_prompt",
)


def _structured_call_scores(
    calm_turns: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Project CALM turns without copying prompts or transcript text."""
    calm: list[dict[str, Any]] = []
    safety: list[dict[str, Any]] = []
    clinical: list[dict[str, Any]] = []
    for turn in calm_turns or []:
        if not isinstance(turn, dict):
            continue
        turn_id = turn.get("turn_id")
        calm.append(
            {
                "turn_id": turn_id,
                "scores": turn.get("calm_scores") or {},
                "confidence": turn.get("calm_confidence") or {},
                "trend": turn.get("trend") or {},
                "significant_changes": turn.get("significant_changes") or {},
            }
        )
        safety.append(
            {"turn_id": turn_id, "safety_state": turn.get("safety_state") or {}}
        )
        clinical.append(
            {
                "turn_id": turn_id,
                "clinical_evaluation": turn.get("clinical_evaluation") or {},
            }
        )
    return {"turns": calm}, {"turns": safety}, {"turns": clinical}


def _scenario_dict(scenario: SakinahScenarioModel) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "sequence": scenario.sequence,
        "title": scenario.title,
        "category": scenario.category,
        "tags": scenario.tags or [],
        "mode": scenario.mode,
        "persona": scenario.persona,
        "age": scenario.age,
        "gender": scenario.gender,
        "language": scenario.language,
        "emotion": scenario.emotion,
        "communicationStyle": scenario.communication_style,
        "initialInformation": scenario.initial_information,
        "hiddenInformation": scenario.hidden_information,
        "disclosure": scenario.disclosure,
        "behaviour": scenario.behaviour,
        "background": scenario.background,
        "additionalFactors": scenario.additional_factors,
        "notes": scenario.notes,
        "freestylePrompt": scenario.freestyle_prompt,
        "createdAt": scenario.created_at.isoformat(),
        "updatedAt": scenario.updated_at.isoformat(),
    }


class SakinahPersistenceClient(BaseDBClient):
    async def get_sakinah_scenarios(
        self, user_id: int, search: str | None = None
    ) -> list[dict[str, Any]]:
        async with self.async_session() as session:
            query = select(SakinahScenarioModel).where(
                SakinahScenarioModel.user_id == user_id
            )
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                search_text = func.concat_ws(
                    " ",
                    SakinahScenarioModel.id,
                    SakinahScenarioModel.title,
                    SakinahScenarioModel.category,
                    cast(SakinahScenarioModel.tags, Text),
                    SakinahScenarioModel.persona,
                    SakinahScenarioModel.language,
                    SakinahScenarioModel.communication_style,
                    SakinahScenarioModel.initial_information,
                    SakinahScenarioModel.hidden_information,
                    SakinahScenarioModel.disclosure,
                    SakinahScenarioModel.behaviour,
                    SakinahScenarioModel.background,
                    SakinahScenarioModel.additional_factors,
                    SakinahScenarioModel.notes,
                    SakinahScenarioModel.freestyle_prompt,
                )
                query = query.where(search_text.ilike(pattern))
            result = await session.execute(
                query.order_by(
                    SakinahScenarioModel.sequence.asc(),
                    SakinahScenarioModel.created_at.asc(),
                )
            )
            return [_scenario_dict(item) for item in result.scalars().all()]

    async def create_sakinah_scenario(
        self,
        user_id: int,
        scenario: dict[str, Any],
        *,
        scenario_id: str | None = None,
        sequence: int | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> dict[str, Any]:
        async with self.async_session() as session:
            # Lock the user row so two simultaneous creates cannot receive the
            # same display sequence for one user.
            await session.execute(
                select(UserModel.id).where(UserModel.id == user_id).with_for_update()
            )
            if sequence is None:
                sequence = (
                    await session.execute(
                        select(
                            func.coalesce(func.max(SakinahScenarioModel.sequence), 0)
                        ).where(SakinahScenarioModel.user_id == user_id)
                    )
                ).scalar_one() + 1
            now = datetime.now(UTC)
            item = SakinahScenarioModel(
                id=scenario_id or str(uuid.uuid4()),
                user_id=user_id,
                sequence=sequence,
                created_at=created_at or now,
                updated_at=updated_at or now,
                **{
                    field: scenario.get(field, [] if field == "tags" else "")
                    for field in SCENARIO_FIELDS
                },
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return _scenario_dict(item)

    async def update_sakinah_scenario(
        self, user_id: int, scenario_id: str, scenario: dict[str, Any]
    ) -> dict[str, Any] | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(SakinahScenarioModel).where(
                    SakinahScenarioModel.id == scenario_id,
                    SakinahScenarioModel.user_id == user_id,
                )
            )
            item = result.scalars().first()
            if item is None:
                return None
            for field in SCENARIO_FIELDS:
                setattr(item, field, scenario.get(field, [] if field == "tags" else ""))
            item.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(item)
            return _scenario_dict(item)

    async def delete_sakinah_scenario(self, user_id: int, scenario_id: str) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(SakinahScenarioModel).where(
                    SakinahScenarioModel.id == scenario_id,
                    SakinahScenarioModel.user_id == user_id,
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def create_sakinah_run(
        self,
        *,
        session_id: str,
        user_id: int,
        agent_id: int,
        run_id: int,
        scenario: str,
        started_at: datetime,
        service_user_agent_id: int | None = None,
        service_user_run_id: int | None = None,
        experiment_mode: str | None = None,
    ) -> SakinahRunModel:
        async with self.async_session() as session:
            item = SakinahRunModel(
                session_id=session_id,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                service_user_agent_id=service_user_agent_id,
                service_user_run_id=service_user_run_id,
                scenario=scenario,
                started_at=started_at,
                experiment_mode=experiment_mode,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

    async def get_sakinah_runs(
        self, user_id: int, limit: int = 50
    ) -> list[SakinahRunModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(SakinahRunModel)
                .where(SakinahRunModel.user_id == user_id)
                .order_by(SakinahRunModel.started_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_sakinah_run(
        self, user_id: int, session_id: str
    ) -> SakinahRunModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(SakinahRunModel).where(
                    SakinahRunModel.session_id == session_id,
                    SakinahRunModel.user_id == user_id,
                )
            )
            return result.scalars().first()

    async def complete_sakinah_run(
        self,
        *,
        user_id: int,
        session_id: str,
        status: str,
        ended_at: datetime,
        transcript: str,
        conversation: list[dict[str, Any]],
        preview_data: dict[str, Any],
        recording_url: str | None = None,
        transcript_url: str | None = None,
        recording_file_reference: dict[str, Any] | None = None,
        calm_turns: list[dict[str, Any]] | None = None,
        timings: dict[str, Any] | None = None,
    ) -> SakinahRunModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(SakinahRunModel)
                .where(
                    SakinahRunModel.session_id == session_id,
                    SakinahRunModel.user_id == user_id,
                )
                .with_for_update()
            )
            item = result.scalars().first()
            if item is None:
                return None
            item.status = status
            item.ended_at = ended_at
            item.transcript = transcript
            item.conversation = conversation
            item.preview_data = preview_data
            item.recording_url = recording_url
            item.transcript_url = transcript_url
            item.recording_file_reference = recording_file_reference or {}
            item.calm_turns = calm_turns or []
            item.timings = timings or {}

            calm_score, safety_score, clinical_evaluation = _structured_call_scores(
                calm_turns
            )
            workflow_run = await session.get(WorkflowRunModel, item.run_id)
            if workflow_run is not None:
                score_result = await session.execute(
                    select(CallScoreModel).where(
                        CallScoreModel.agent_run_id == item.run_id
                    )
                )
                call_score = score_result.scalars().first()
                if call_score is None:
                    session.add(
                        CallScoreModel(
                            agent_run_id=item.run_id,
                            calm_score=calm_score,
                            safety_score=safety_score,
                            clinical_evaluation=clinical_evaluation,
                        )
                    )
                else:
                    call_score.calm_score = calm_score
                    call_score.safety_score = safety_score
                    call_score.clinical_evaluation = clinical_evaluation
                workflow_run.latency_metrics = {
                    **(workflow_run.latency_metrics or {}),
                    "simulation": timings or {},
                }
            await session.commit()
            await session.refresh(item)
            return item

    async def get_workflow_run_artifacts_for_user(
        self, user_id: int, run_id: int
    ) -> dict[str, Any] | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunModel)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(
                    WorkflowRunModel.id == run_id,
                    WorkflowModel.user_id == user_id,
                )
            )
            run = result.scalars().first()
            if run is None:
                return None
            return {
                "recording_url": run.recording_url,
                "transcript_url": run.transcript_url,
                "recording_file_reference": (run.extra or {}).get("recordings", {}),
            }

    async def sync_sakinah_run_artifacts(self, workflow_run_id: int) -> None:
        """Copy native workflow artifact references into the white-label run."""
        async with self.async_session() as session:
            run_result = await session.execute(
                select(WorkflowRunModel, WorkflowModel.user_id)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(WorkflowRunModel.id == workflow_run_id)
            )
            workflow_row = run_result.first()
            if workflow_row is None:
                return
            workflow_run, workflow_owner_id = workflow_row
            white_label_result = await session.execute(
                select(SakinahRunModel).where(
                    SakinahRunModel.user_id == workflow_owner_id,
                    or_(
                        SakinahRunModel.run_id == workflow_run_id,
                        SakinahRunModel.service_user_run_id == workflow_run_id,
                    ),
                )
            )
            white_label_run = white_label_result.scalars().first()
            if white_label_run is None:
                return
            role = (
                "service_user"
                if white_label_run.service_user_run_id == workflow_run_id
                else "sakinah"
            )
            references = dict(white_label_run.recording_file_reference or {})
            role_recordings = (workflow_run.extra or {}).get("recordings", {})
            # Artifact fields are written independently. Do not replace a
            # previously saved reference with an empty object if reconciliation
            # runs between the recording URL update and the recordings metadata
            # update.
            if role_recordings:
                existing_role_recordings = references.get(role)
                if isinstance(existing_role_recordings, dict):
                    references[role] = {
                        **existing_role_recordings,
                        **role_recordings,
                    }
                else:
                    references[role] = role_recordings
            white_label_run.recording_file_reference = references
            if role == "sakinah":
                if workflow_run.recording_url:
                    white_label_run.recording_url = workflow_run.recording_url
                if workflow_run.transcript_url:
                    white_label_run.transcript_url = workflow_run.transcript_url
            await session.commit()
