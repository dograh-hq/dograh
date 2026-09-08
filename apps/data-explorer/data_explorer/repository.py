"""Parameterized PostgreSQL read repository for the v1.46.0.3 durable schema.

There is deliberately no generic execute method: browser input is converted into
bounded filters and every statement below is a static SELECT/SHOW query.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def rows(result) -> list[dict[str, Any]]:
    return [json_safe(dict(item)) for item in result.mappings().all()]


CALL_COLUMNS = """
 r.id AS run_id, r.call_id, r.service_user_id AS caller_id,
 r.caller_identifier, r.telephone_number, r.started_at, r.connected_at, r.ended_at,
 r.duration_seconds, r.call_status, r.state, r.direction, r.call_type,
 r.scenario_id, r.scenario_name, r.telephony_provider, r.provider_call_id,
 r.model_provider, r.stt_provider, r.tts_provider, r.termination_reason,
 r.storage_backend, r.recording_object_key, r.transcript_object_key,
 r.recording_duration_seconds, r.recording_format, r.recording_size_bytes,
 r.full_transcript, r.latency_metrics, r.debug_metadata, r.extra, r.usage_info,
 r.cost_info, r.gathered_context, r.created_at, w.id AS workflow_id,
 w.name AS agent, w.organization_id
"""


class ExplorerRepository:
    def __init__(self, database_url: str):
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)

    async def close(self) -> None:
        await self.engine.dispose()

    async def _fetch(self, sql: str, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            # Defence in depth: the deployed role is SELECT-only and each
            # Explorer transaction is additionally forced read-only.
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            result = await connection.execute(text(sql), values or {})
            return rows(result)

    async def _one(self, sql: str, values: dict[str, Any]) -> dict[str, Any] | None:
        result = await self._fetch(sql, values)
        return result[0] if result else None

    async def list_calls(self, filters: dict[str, Any]) -> dict[str, Any]:
        clauses = ["1=1"]
        values: dict[str, Any] = {}
        for field in ("call_id", "run_id", "caller", "agent", "status"):
            value = filters.get(field)
            if not value:
                continue
            if field == "call_id":
                clauses.append("r.call_id ILIKE :call_id")
                values[field] = f"%{value}%"
            elif field == "run_id":
                clauses.append("CAST(r.id AS TEXT) = :run_id")
                values[field] = str(value)
            elif field == "caller":
                clauses.append("(r.service_user_id::text ILIKE :caller OR r.caller_identifier ILIKE :caller OR r.telephone_number ILIKE :caller)")
                values[field] = f"%{value}%"
            elif field == "agent":
                clauses.append("w.name ILIKE :agent")
                values[field] = f"%{value}%"
            else:
                clauses.append("r.call_status = :status")
                values[field] = value
        if filters.get("start_date"):
            clauses.append("r.started_at >= :start_date")
            values["start_date"] = filters["start_date"]
        if filters.get("end_date"):
            clauses.append("r.started_at < (:end_date::date + INTERVAL '1 day')")
            values["end_date"] = filters["end_date"]
        if filters.get("risk"):
            clauses.append("EXISTS (SELECT 1 FROM call_events ce WHERE ce.agent_run_id = r.id AND ce.severity = :risk)")
            values["risk"] = filters["risk"]
        where = " AND ".join(clauses)
        page = max(1, int(filters.get("page", 1)))
        page_size = min(100, max(1, int(filters.get("page_size", 25))))
        values.update({"limit": page_size, "offset": (page - 1) * page_size})
        items = await self._fetch(
            f"SELECT {CALL_COLUMNS} FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id WHERE {where} ORDER BY r.started_at DESC NULLS LAST, r.id DESC LIMIT :limit OFFSET :offset",
            values,
        )
        total = await self._one(
            f"SELECT count(*) AS count FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id WHERE {where}", values
        )
        return {"items": items, "page": page, "page_size": page_size, "total": int(total["count"] if total else 0)}

    async def get_call(self, call_id: str) -> dict[str, Any] | None:
        return await self._one(
            f"SELECT {CALL_COLUMNS} FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id WHERE r.call_id=:call_id",
            {"call_id": call_id},
        )

    async def conversation(self, run_id: int) -> list[dict[str, Any]]:
        return await self._fetch(
            "SELECT id, agent_run_id, speaker, sequence_number, start_ms, end_ms, transcript, created_at, calm_score, safety_score, clinical_score FROM utterances WHERE agent_run_id=:run_id ORDER BY sequence_number ASC",
            {"run_id": run_id},
        )

    async def scores(self, run_id: int) -> dict[str, Any]:
        score = await self._one(
            "SELECT id, calm_score, safety_score, clinical_evaluation, created_at FROM call_scores WHERE agent_run_id=:run_id",
            {"run_id": run_id},
        )
        events = await self._fetch(
            "SELECT id, event_type, occurred_at, severity, payload FROM call_events WHERE agent_run_id=:run_id ORDER BY occurred_at ASC, id ASC",
            {"run_id": run_id},
        )
        return {"score": score or {}, "events": events}

    async def files(self, call: dict[str, Any]) -> list[dict[str, Any]]:
        recordings = await self._fetch(
            "SELECT id, agent_run_id, storage_backend, object_key, duration_seconds, format, size_bytes, checksum_sha256, track, created_at FROM recordings WHERE agent_run_id=:run_id ORDER BY track ASC",
            {"run_id": call["run_id"]},
        )
        result = [
            {
                **item,
                "file_id": f"recording:{item['id']}",
                "file_name": f"{item['track'] or 'recording'}.{item['format'] or 'wav'}",
                "mime_type": _mime(item["format"]),
                "kind": "audio",
            }
            for item in recordings
        ]
        transcript_key = call.get("transcript_object_key")
        if transcript_key:
            result.append({
                "file_id": "transcript",
                "agent_run_id": call["run_id"],
                "storage_backend": call.get("storage_backend"),
                "object_key": transcript_key,
                "file_name": "transcript.txt",
                "mime_type": "text/plain; charset=utf-8",
                "size_bytes": None,
                "created_at": call.get("created_at"),
                "kind": "text",
            })
        return result

    async def file_for_call(self, call: dict[str, Any], file_id: str) -> dict[str, Any] | None:
        return next((item for item in await self.files(call) if item["file_id"] == file_id), None)

    async def recording_file(self, recording_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Resolve a recording ID through its owning call, never an object key."""
        row = await self._one(
            "SELECT r.call_id FROM recordings cr JOIN workflow_runs r ON r.id=cr.agent_run_id WHERE cr.id=:recording_id",
            {"recording_id": recording_id},
        )
        if row is None:
            return None
        call = await self.get_call(row["call_id"])
        if call is None:
            return None
        file = await self.file_for_call(call, f"recording:{recording_id}")
        return (call, file) if file else None

    async def memory(self, call: dict[str, Any]) -> dict[str, Any]:
        caller_id = call.get("caller_id")
        if not caller_id:
            return {"retrieved_for_call": [], "written_during_call": [], "current": [], "history": [], "retrieval_status": "unavailable: retrieval events are not persisted by v1.46.0.3"}
        memory_fields = "id, service_user_id, memory_type, memory_text, importance, confidence, sensitivity, source_agent_run_id, source_utterance_id, internal_context_allowed, verbal_reference_allowed, explicit_detail_allowed, created_at, last_confirmed_at, expires_at, active"
        current = await self._fetch(f"SELECT {memory_fields} FROM memories WHERE service_user_id=:caller_id ORDER BY created_at ASC", {"caller_id": caller_id})
        written = [item for item in current if item.get("source_agent_run_id") == call["run_id"]]
        before = [item for item in current if item.get("created_at") and call.get("started_at") and item["created_at"] <= call["started_at"]]
        sources = await self._fetch(
            "SELECT ms.id, ms.memory_id, ms.agent_run_id, ms.utterance_id, ms.source_excerpt, ms.created_at FROM memory_sources ms JOIN memories m ON m.id=ms.memory_id WHERE m.service_user_id=:caller_id ORDER BY ms.created_at ASC",
            {"caller_id": caller_id},
        )
        return {
            "available_before_call": before,
            "retrieved_for_call": [],
            "written_during_call": written,
            "current": current,
            "history": sources,
            "retrieval_status": "unavailable: retrieval events are not persisted by v1.46.0.3",
        }

    async def user(self, caller_id: str) -> dict[str, Any] | None:
        return await self._one(
            "SELECT id, organization_id, preferred_name, first_seen_at, last_seen_at, memory_enabled, status, created_at, updated_at FROM service_users WHERE id=:caller_id",
            {"caller_id": caller_id},
        )

    async def user_calls(self, caller_id: str) -> list[dict[str, Any]]:
        return await self._fetch(
            f"SELECT {CALL_COLUMNS} FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id WHERE r.service_user_id=:caller_id ORDER BY r.started_at DESC NULLS LAST, r.id DESC",
            {"caller_id": caller_id},
        )

    async def metrics(self) -> dict[str, int]:
        row = await self._one("""
            SELECT
              count(*) AS total_calls,
              count(*) FILTER (WHERE started_at >= date_trunc('day', now())) AS calls_today,
              count(DISTINCT service_user_id) FILTER (WHERE service_user_id IS NOT NULL) AS unique_callers,
              (SELECT count(*) FROM memories) AS stored_memories,
              (SELECT count(DISTINCT agent_run_id) FROM recordings) AS calls_with_audio,
              (SELECT count(DISTINCT agent_run_id) FROM call_events WHERE severity IS NOT NULL) AS calls_with_safety_alerts
            FROM workflow_runs
        """, {})
        return {key: int(value or 0) for key, value in (row or {}).items()}

    async def schema(self) -> dict[str, Any]:
        table_names = ("workflow_runs", "utterances", "recordings", "call_scores", "call_events", "service_users", "caller_identifiers", "memories", "memory_sources", "privacy_permissions", "artifact_replication_status")
        tables = await self._fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name = ANY(:tables)
            ORDER BY table_name
        """, {"tables": list(table_names)})
        columns = await self._fetch("""
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns WHERE table_schema='public' AND table_name = ANY(:tables)
            ORDER BY table_name, ordinal_position
        """, {"tables": list(table_names)})
        constraints = await self._fetch("""
            SELECT tc.table_name, tc.constraint_name, tc.constraint_type, kcu.column_name,
                   ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints tc
            LEFT JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
            LEFT JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema
            WHERE tc.table_schema='public' AND tc.table_name = ANY(:tables)
            ORDER BY tc.table_name, tc.constraint_name
        """, {"tables": list(table_names)})
        indexes = await self._fetch("""
            SELECT tablename AS table_name, indexname AS index_name, indexdef AS definition
            FROM pg_indexes WHERE schemaname='public' AND tablename = ANY(:tables)
            ORDER BY tablename, indexname
        """, {"tables": list(table_names)})
        return {"tables": tables, "columns": columns, "constraints": constraints, "indexes": indexes}

    async def readonly_status(self) -> dict[str, Any]:
        return await self._one("""
          SELECT current_user AS database_user,
                 current_setting('transaction_read_only') AS transaction_read_only,
                 has_table_privilege(current_user, 'workflow_runs', 'INSERT') AS can_insert,
                 has_table_privilege(current_user, 'workflow_runs', 'UPDATE') AS can_update,
                 has_table_privilege(current_user, 'workflow_runs', 'DELETE') AS can_delete,
                 has_schema_privilege(current_user, 'public', 'CREATE') AS can_create
        """, {}) or {}


def _mime(file_format: str | None) -> str:
    return {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4", "ogg": "audio/ogg"}.get((file_format or "").lower(), "application/octet-stream")
