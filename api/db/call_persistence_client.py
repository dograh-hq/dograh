"""Database operations for the durable call, replay, and memory model.

This module deliberately keeps the structured call record separate from the
legacy artifact URL fields.  The latter are retained for integrations and old
clients; the tables below are the canonical white-label data model.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from api.constants import MEMORY_MAX_RESULTS, MEMORY_MIN_SIMILARITY
from api.db.base_client import BaseDBClient
from api.db.models import (
    CallEventModel,
    CallRecordingModel,
    CallScoreModel,
    CallUtteranceModel,
    MemoryModel,
    MemorySourceModel,
    ServiceUserModel,
    WorkflowModel,
    WorkflowRunModel,
)


def normalize_caller_identifier(identifier: str | None) -> str | None:
    if not identifier:
        return None
    normalized = "".join(identifier.strip().lower().split())
    return normalized or None


def caller_identifier_hash(identifier: str | None) -> str | None:
    normalized = normalize_caller_identifier(identifier)
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _timestamp_to_ms(value: str | None, started_at: datetime | None) -> int | None:
    if not value or not started_at:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0, int((parsed - started_at).total_seconds() * 1000))
    except (TypeError, ValueError):
        return None


def _utterance_events(events: list[dict[str, Any]], started_at: datetime | None) -> list[dict[str, Any]]:
    utterances: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload") or {}
        if event_type == "rtf-user-transcription" and payload.get("final") is not True:
            continue
        if event_type not in {"rtf-user-transcription", "rtf-bot-text"}:
            continue
        text = str(payload.get("text") or "").strip()
        if not text:
            continue
        speaker = "user" if event_type == "rtf-user-transcription" else "assistant"
        start_value = payload.get("timestamp") or event.get("timestamp")
        utterances.append(
            {
                "speaker": speaker,
                "sequence_number": len(utterances) + 1,
                "start_ms": _timestamp_to_ms(start_value, started_at),
                "end_ms": _timestamp_to_ms(payload.get("end_timestamp"), started_at),
                "transcript": text,
                "calm_score": payload.get("calm_score"),
                "safety_score": payload.get("safety_score"),
                "clinical_score": payload.get("clinical_score"),
            }
        )
    return utterances


def _transcript_from_events(events: list[dict[str, Any]]) -> str:
    lines = []
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload") or {}
        if event_type == "rtf-user-transcription" and payload.get("final") is not True:
            continue
        if event_type not in {"rtf-user-transcription", "rtf-bot-text"}:
            continue
        text = str(payload.get("text") or "").strip()
        if text:
            speaker = "user" if event_type == "rtf-user-transcription" else "assistant"
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _score_payload(run: WorkflowRunModel) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    gathered = run.gathered_context or {}
    annotations = run.annotations or {}
    calm = gathered.get("calm_score") or gathered.get("calm_scores") or annotations.get("calm_score") or {}
    safety = gathered.get("safety_score") or gathered.get("safety_scores") or annotations.get("safety_score") or {}
    clinical = (
        gathered.get("clinical_evaluation")
        or annotations.get("clinical_evaluation")
        or {}
    )
    return calm if isinstance(calm, dict) else {}, safety if isinstance(safety, dict) else {}, clinical if isinstance(clinical, dict) else {}


class CallPersistenceClient(BaseDBClient):
    async def get_or_create_service_user(
        self,
        organization_id: int,
        caller_identifier: str,
        *,
        preferred_name: str | None = None,
    ) -> tuple[ServiceUserModel, bool]:
        identifier_hash = caller_identifier_hash(caller_identifier)
        if identifier_hash is None:
            raise ValueError("caller_identifier is required")
        async with self.async_session() as session:
            result = await session.execute(
                select(ServiceUserModel)
                .where(
                    ServiceUserModel.organization_id == organization_id,
                    ServiceUserModel.caller_identifier_hash == identifier_hash,
                )
                .with_for_update()
            )
            item = result.scalars().first()
            created = item is None
            now = datetime.now(UTC)
            if item is None:
                item = ServiceUserModel(
                    id=str(uuid.uuid4()),
                    organization_id=organization_id,
                    caller_identifier_hash=identifier_hash,
                    preferred_name=preferred_name,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                session.add(item)
            else:
                item.last_seen_at = now
                if preferred_name and not item.preferred_name:
                    item.preferred_name = preferred_name
            try:
                await session.commit()
            except IntegrityError:
                # Two simultaneous calls from the same caller can both miss
                # the row before either commits. The unique constraint is the
                # arbiter; reload the winner instead of failing the call.
                await session.rollback()
                result = await session.execute(
                    select(ServiceUserModel).where(
                        ServiceUserModel.organization_id == organization_id,
                        ServiceUserModel.caller_identifier_hash == identifier_hash,
                    )
                )
                item = result.scalars().first()
                if item is None:
                    raise
                item.last_seen_at = datetime.now(UTC)
                if preferred_name and not item.preferred_name:
                    item.preferred_name = preferred_name
                await session.commit()
                created = False
            await session.refresh(item)
            return item, created

    async def mark_first_use_explanation_shown(self, service_user_id: str) -> None:
        async with self.async_session() as session:
            item = await session.get(ServiceUserModel, service_user_id)
            if item is not None and not item.first_use_explanation_shown:
                item.first_use_explanation_shown = True
                await session.commit()

    async def get_permitted_memories(
        self,
        service_user_id: str,
        *,
        verified: bool = False,
        query_embedding: list[float] | None = None,
        limit: int = MEMORY_MAX_RESULTS,
        min_similarity: float = MEMORY_MIN_SIMILARITY,
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        async with self.async_session() as session:
            query = select(MemoryModel).where(
                MemoryModel.service_user_id == service_user_id,
                MemoryModel.active.is_(True),
                MemoryModel.internal_context_allowed.is_(True),
                or_(MemoryModel.expires_at.is_(None), MemoryModel.expires_at > now),
            )
            if not verified:
                query = query.where(MemoryModel.sensitivity.in_(("low", "normal")))

            if query_embedding and len(query_embedding) == 1536:
                distance = MemoryModel.embedding.cosine_distance(query_embedding)
                query = query.where(MemoryModel.embedding.is_not(None)).add_columns(distance.label("distance"))
                result = await session.execute(query.order_by(distance.asc()).limit(limit))
                rows = result.all()
                memories = []
                for memory, distance_value in rows:
                    similarity = 1 - float(distance_value)
                    if similarity >= min_similarity:
                        memories.append(self._memory_dict(memory, similarity))
                return memories
            if query_embedding:
                return []

            result = await session.execute(
                query.order_by(MemoryModel.importance.desc(), MemoryModel.created_at.desc()).limit(limit)
            )
            return [self._memory_dict(memory, None) for memory in result.scalars().all()]

    @staticmethod
    def _memory_dict(memory: MemoryModel, similarity: float | None) -> dict[str, Any]:
        return {
            "id": memory.id,
            "memory_type": memory.memory_type,
            "memory_text": memory.memory_text,
            "importance": memory.importance,
            "confidence": memory.confidence,
            "sensitivity": memory.sensitivity,
            "internal_context_allowed": memory.internal_context_allowed,
            "verbal_reference_allowed": memory.verbal_reference_allowed,
            "explicit_detail_allowed": memory.explicit_detail_allowed,
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
            "similarity": similarity,
        }

    async def create_or_confirm_memory(self, values: dict[str, Any]) -> MemoryModel:
        async with self.async_session() as session:
            existing_result = await session.execute(
                select(MemoryModel).where(
                    MemoryModel.service_user_id == values["service_user_id"],
                    MemoryModel.memory_type == values["memory_type"],
                    MemoryModel.memory_text == values["memory_text"],
                    MemoryModel.active.is_(True),
                )
            )
            item = existing_result.scalars().first()
            now = datetime.now(UTC)
            if item is None:
                item = MemoryModel(
                    id=values.get("id") or str(uuid.uuid4()),
                    service_user_id=values["service_user_id"],
                    memory_type=values["memory_type"],
                    memory_text=values["memory_text"],
                    embedding=values.get("embedding"),
                    importance=values.get("importance", 0.5),
                    confidence=values.get("confidence", 0.5),
                    sensitivity=values.get("sensitivity", "normal"),
                    source_agent_run_id=values.get("source_agent_run_id"),
                    source_utterance_id=values.get("source_utterance_id"),
                    internal_context_allowed=values.get("internal_context_allowed", True),
                    verbal_reference_allowed=values.get("verbal_reference_allowed", False),
                    explicit_detail_allowed=values.get("explicit_detail_allowed", False),
                    last_confirmed_at=now,
                    expires_at=values.get("expires_at"),
                )
                session.add(item)
            else:
                item.last_confirmed_at = now
                item.confidence = max(item.confidence or 0, values.get("confidence", 0.5))
                if values.get("embedding") is not None:
                    item.embedding = values["embedding"]
            await session.commit()
            await session.refresh(item)
            if values.get("source_agent_run_id"):
                source_query = select(MemorySourceModel.id).where(
                    MemorySourceModel.memory_id == item.id,
                    MemorySourceModel.agent_run_id == values["source_agent_run_id"],
                    MemorySourceModel.utterance_id == values.get("source_utterance_id"),
                )
                if (await session.execute(source_query)).scalar_one_or_none() is None:
                    session.add(
                        MemorySourceModel(
                            memory_id=item.id,
                            agent_run_id=values["source_agent_run_id"],
                            utterance_id=values.get("source_utterance_id"),
                            source_excerpt=values.get("source_excerpt"),
                        )
                    )
                await session.commit()
            return item

    async def persist_call_snapshot(
        self,
        workflow_run_id: int,
        *,
        events: list[dict[str, Any]] | None = None,
        transcript_text: str | None = None,
    ) -> WorkflowRunModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id).with_for_update()
            )
            run = result.scalars().first()
            if run is None:
                return None

            event_list = events or (run.logs or {}).get("realtime_feedback_events", [])
            run.full_transcript = transcript_text or run.full_transcript or _transcript_from_events(event_list)
            if run.duration_seconds is None and run.started_at and run.ended_at:
                run.duration_seconds = max(0, (run.ended_at - run.started_at).total_seconds())

            for item in _utterance_events(event_list, run.started_at):
                existing_result = await session.execute(
                    select(CallUtteranceModel).where(
                        CallUtteranceModel.agent_run_id == workflow_run_id,
                        CallUtteranceModel.sequence_number == item["sequence_number"],
                    )
                )
                utterance = existing_result.scalars().first()
                if utterance is None:
                    session.add(CallUtteranceModel(agent_run_id=workflow_run_id, **item))

            calm, safety, clinical = _score_payload(run)
            score_result = await session.execute(
                select(CallScoreModel).where(CallScoreModel.agent_run_id == workflow_run_id)
            )
            scores = score_result.scalars().first()
            if scores is None:
                session.add(
                    CallScoreModel(
                        agent_run_id=workflow_run_id,
                        calm_score=calm,
                        safety_score=safety,
                        clinical_evaluation=clinical,
                    )
                )
            else:
                scores.calm_score = calm
                scores.safety_score = safety
                scores.clinical_evaluation = clinical

            gathered = run.gathered_context or {}
            risk_events = gathered.get("risk_events") or gathered.get("escalation_events") or []
            if isinstance(risk_events, list):
                for payload in risk_events:
                    if not isinstance(payload, dict):
                        continue
                    event_id = payload.get("event_id") or str(uuid.uuid4())
                    exists = await session.get(CallEventModel, event_id)
                    if exists is None:
                        session.add(
                            CallEventModel(
                                id=event_id,
                                agent_run_id=workflow_run_id,
                                event_type=str(payload.get("event_type") or "risk"),
                                severity=payload.get("severity"),
                                payload={k: v for k, v in payload.items() if k not in {"event_id", "transcript"}},
                            )
                        )
            termination_reason = run.termination_reason or gathered.get("termination_reason")
            if termination_reason:
                existing_termination = await session.execute(
                    select(CallEventModel.id).where(
                        CallEventModel.agent_run_id == workflow_run_id,
                        CallEventModel.event_type == "termination",
                    )
                )
                if existing_termination.scalar_one_or_none() is None:
                    session.add(
                        CallEventModel(
                            agent_run_id=workflow_run_id,
                            event_type="termination",
                            payload={"reason": termination_reason},
                        )
                    )
            await session.commit()
            await session.refresh(run)
            return run

    async def get_utterances_for_run(self, workflow_run_id: int) -> list[CallUtteranceModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(CallUtteranceModel)
                .where(CallUtteranceModel.agent_run_id == workflow_run_id)
                .order_by(CallUtteranceModel.sequence_number.asc())
            )
            return list(result.scalars().all())

    async def upsert_call_recording(
        self,
        *,
        agent_run_id: int,
        storage_backend: str,
        object_key: str,
        duration_seconds: float | None,
        format: str,
        size_bytes: int | None,
        track: str = "mixed",
    ) -> CallRecordingModel:
        async with self.async_session() as session:
            result = await session.execute(
                select(CallRecordingModel).where(
                    CallRecordingModel.agent_run_id == agent_run_id,
                    CallRecordingModel.track == track,
                )
            )
            item = result.scalars().first()
            if item is None:
                item = CallRecordingModel(
                    agent_run_id=agent_run_id,
                    storage_backend=storage_backend,
                    object_key=object_key,
                    duration_seconds=duration_seconds,
                    format=format,
                    size_bytes=size_bytes,
                    track=track,
                )
                session.add(item)
            else:
                item.storage_backend = storage_backend
                item.object_key = object_key
                item.duration_seconds = duration_seconds
                item.format = format
                item.size_bytes = size_bytes
            run = await session.get(WorkflowRunModel, agent_run_id)
            if run is not None and track == "mixed":
                run.recording_object_key = object_key
                run.recording_duration_seconds = duration_seconds
                run.recording_format = format
                run.recording_size_bytes = size_bytes
            await session.commit()
            await session.refresh(item)
            return item

    async def get_call_replay_for_user(
        self, call_id: str, *, organization_id: int | None, is_superuser: bool = False
    ) -> dict[str, Any] | None:
        async with self.async_session() as session:
            query = (
                select(WorkflowRunModel)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(WorkflowRunModel.call_id == call_id)
                .options(joinedload(WorkflowRunModel.workflow))
            )
            if not is_superuser:
                query = query.where(WorkflowModel.organization_id == organization_id)
            run = (await session.execute(query)).scalars().first()
            if run is None:
                return None
            recordings = await session.execute(
                select(CallRecordingModel)
                .where(CallRecordingModel.agent_run_id == run.id)
                .order_by(CallRecordingModel.track.asc())
            )
            utterances = await session.execute(
                select(CallUtteranceModel)
                .where(CallUtteranceModel.agent_run_id == run.id)
                .order_by(CallUtteranceModel.sequence_number.asc())
            )
            recording = next(
                (item for item in recordings.scalars().all() if item.track == "mixed"),
                None,
            )
            return {
                "call_id": run.call_id,
                "agent_run_id": run.id,
                "storage_backend": recording.storage_backend if recording else run.storage_backend,
                "recording_key": recording.object_key if recording else run.recording_object_key or run.recording_url,
                "transcript": run.full_transcript,
                "utterances": [
                    {
                        "id": item.id,
                        "speaker": item.speaker,
                        "sequence_number": item.sequence_number,
                        "start_ms": item.start_ms,
                        "end_ms": item.end_ms,
                        "transcript": item.transcript,
                        "calm_score": item.calm_score,
                        "safety_score": item.safety_score,
                        "clinical_score": item.clinical_score,
                    }
                    for item in utterances.scalars().all()
                ],
            }

    async def get_workflow_run_by_artifact_key(self, object_key: str) -> WorkflowRunModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunModel)
                .options(joinedload(WorkflowRunModel.workflow))
                .outerjoin(
                    CallRecordingModel,
                    CallRecordingModel.agent_run_id == WorkflowRunModel.id,
                )
                .where(
                    or_(
                        CallRecordingModel.object_key == object_key,
                        WorkflowRunModel.recording_url == object_key,
                        WorkflowRunModel.transcript_url == object_key,
                    )
                )
                .limit(1)
            )
            return result.scalars().first()

    async def get_workflow_run_by_recording_key(self, object_key: str) -> WorkflowRunModel | None:
        return await self.get_workflow_run_by_artifact_key(object_key)
