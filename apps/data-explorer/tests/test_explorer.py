from __future__ import annotations

import io
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from data_explorer.audit import FileAuditSink
from data_explorer.config import Settings
from data_explorer.main import build_call_export, create_app
from data_explorer.storage import ObjectNotAvailable, safe_filename


CALL = {
    "run_id": 19, "call_id": "call-19", "caller_id": "user-1", "workflow_id": 4,
    "agent": "Sakinah", "started_at": "2026-01-01T10:00:00+00:00", "created_at": "2026-01-01T10:00:00+00:00",
    "call_status": "completed", "full_transcript": "user: hello\nassistant: hello", "storage_backend": "s3",
    "transcript_object_key": "transcripts/2026/01/user-1/call-19/transcript.txt", "caller_identifier": "+440000000000",
    "telephone_number": "+440000000000", "recording_object_key": "recordings/2026/01/user-1/call-19/call.wav",
}


class FakeRepository:
    async def close(self): pass
    async def metrics(self): return {"total_calls": 1, "calls_today": 1, "unique_callers": 1, "stored_memories": 2, "calls_with_audio": 1, "calls_with_safety_alerts": 0}
    async def list_calls(self, _): return {"items": [CALL], "page": 1, "page_size": 25, "total": 1}
    async def get_call(self, call_id): return CALL if call_id == "call-19" else None
    async def conversation(self, _): return [{"id": "u1", "speaker": "user", "sequence_number": 1, "transcript": "hello"}]
    async def scores(self, _): return {"score": {"calm_score": {"value": 0.8}, "clinical_evaluation": {}}, "events": []}
    async def files(self, _): return [{"file_id": "recording:r1", "object_key": "recordings/a.wav", "file_name": "mixed.wav", "kind": "audio", "format": "wav", "storage_backend": "s3"}]
    async def file_for_call(self, _, file_id): return (await self.files(CALL))[0] if file_id == "recording:r1" else None
    async def recording_file(self, file_id): return (CALL, (await self.files(CALL))[0]) if file_id == "r1" else None
    async def memory(self, _): return {"available_before_call": [{"id": "old"}], "retrieved_for_call": [], "written_during_call": [{"id": "new", "source_agent_run_id": 19}], "current": [{"id": "old"}, {"id": "new"}], "history": [{"memory_id": "new", "agent_run_id": 19}], "retrieval_status": "unavailable: retrieval events are not persisted by v1.46.0.3"}
    async def user(self, caller_id): return {"id": caller_id, "preferred_name": "Example"} if caller_id == "user-1" else None
    async def user_calls(self, caller_id): return [CALL] if caller_id == "user-1" else []
    async def schema(self): return {"tables": [{"table_name": "workflow_runs"}], "columns": [], "constraints": [], "indexes": []}
    async def readonly_status(self): return {"transaction_read_only": "on", "can_insert": False, "can_update": False, "can_delete": False, "can_create": False}


class FakeStore:
    async def presigned_download(self, key, filename, *, inline): return f"https://object.test/{key}?expires=300"
    async def bytes(self, key, max_bytes):
        if "missing" in key: raise ObjectNotAvailable()
        return b"audio bytes"


@pytest.fixture
def app(tmp_path):
    settings = Settings("postgresql+asyncpg://readonly@example.invalid/calmos", "test-admin-token", str(tmp_path / "audit.jsonl"), "bucket", "eu-west-2", None, None, None, 300, 1024 * 1024)
    return create_app(settings, FakeRepository(), FakeStore(), FileAuditSink(settings.audit_log_path))


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def auth(): return {"Authorization": "Bearer test-admin-token"}


@pytest.mark.asyncio
async def test_authentication_denies_by_default(client):
    assert (await client.get("/api/calls")).status_code == 401


@pytest.mark.asyncio
async def test_call_listing_search_pagination_and_lookup(client):
    listed = await client.get("/api/calls?caller=example&page=1&page_size=25", headers=auth())
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert (await client.get("/api/calls/call-19", headers=auth())).json()["run_id"] == 19
    assert (await client.get("/api/calls/nope", headers=auth())).status_code == 404


@pytest.mark.asyncio
async def test_conversation_memory_and_user_association(client):
    assert (await client.get("/api/calls/call-19/conversation", headers=auth())).json()[0]["speaker"] == "user"
    memory = (await client.get("/api/calls/call-19/memory", headers=auth())).json()
    assert memory["written_during_call"][0]["source_agent_run_id"] == 19
    assert (await client.get("/api/users/user-1/calls", headers=auth())).json()[0]["call_id"] == "call-19"


@pytest.mark.asyncio
async def test_export_schema_files_and_presigned_audio(client):
    export = (await client.get("/api/calls/call-19/export", headers=auth())).json()
    assert export["schema_version"] == "1.0" and export["memory"]["available_before_call"]
    download = await client.get("/api/calls/call-19/files/recording:r1/download?return_url=true", headers=auth())
    assert download.json()["url"].startswith("https://object.test/")
    assert (await client.get("/api/files/r1", headers=auth())).json()["file_name"] == "mixed.wav"
    package = await client.get("/api/calls/call-19/package", headers=auth())
    with ZipFile(io.BytesIO(package.content)) as archive:
        assert {"call.json", "memory.json", "transcript.txt", "files/mixed.wav"}.issubset(archive.namelist())


@pytest.mark.asyncio
async def test_schema_and_permission_evidence(client):
    assert (await client.get("/api/schema", headers=auth())).json()["tables"]
    status = (await client.get("/api/read-only-status", headers=auth())).json()
    assert status["transaction_read_only"] == "on"
    assert not any(status[key] for key in ("can_insert", "can_update", "can_delete", "can_create"))


def test_filename_sanitisation_prevents_path_traversal():
    assert safe_filename("../../unsafe/recording.wav") == "recording.wav"
    assert safe_filename("..\\evil.mp3") == "evil.mp3"


def test_export_preserves_memory_provenance_and_no_binary_audio():
    result = build_call_export(CALL, [], {"score": {}, "events": []}, {"available_before_call": [{"id": "before"}], "retrieved_for_call": [], "written_during_call": [{"id": "new"}], "current": [], "history": []}, [{"file_name": "mixed.wav", "object_key": "key", "size_bytes": 1}])
    assert result["memory"]["available_before_call"][0]["id"] == "before"
    assert result["files"][0]["object_key"] == "key"
