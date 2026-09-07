import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.services import storage_audit


def _run(**overrides):
    values = {
        "id": 7,
        "workflow_id": 3,
        "storage_backend": "minio",
        "is_completed": True,
        "transcript_object_key": "transcripts/7.txt",
        "transcript_url": None,
        "full_transcript": "ASSISTANT: hello",
        "recording_object_key": "recordings/7/call.wav",
        "extra": {},
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Storage:
    async def aget_file_metadata(self, key):
        if key.endswith("transcripts/7.txt"):
            return {"size": 16, "content_type": "text/plain"}
        return None

    async def adownload_file(self, key, path):
        if key.endswith("transcripts/7.txt"):
            await asyncio.to_thread(
                Path(path).write_bytes, b"ASSISTANT: hello"
            )
            return True
        return False


@pytest.mark.asyncio
async def test_audit_reports_verified_database_and_missing_audio(monkeypatch):
    monkeypatch.setattr(storage_audit, "storage_fs", _Storage())

    async def get_run(*args, **kwargs):
        return _run()

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(storage_audit.db_client, "get_workflow_run", get_run)
    monkeypatch.setattr(storage_audit.db_client, "get_utterances_for_run", empty)
    monkeypatch.setattr(storage_audit.db_client, "get_call_recordings_for_run", empty)

    result = await storage_audit.audit_run_storage(7, organization_id=11)

    assert result["postgres"]["status"] == "verified"
    assert result["postgres"]["transcript_status"] == "verified"
    assert result["minio"]["status"] == "missing"
    assert result["minio"]["objects"][0]["status"] == "verified"


@pytest.mark.asyncio
async def test_audit_does_not_expect_audio_for_an_initialized_run(monkeypatch):
    monkeypatch.setattr(storage_audit, "storage_fs", _Storage())

    async def get_run(*args, **kwargs):
        return _run(
            is_completed=False,
            transcript_object_key=None,
            transcript_url=None,
            full_transcript=None,
        )

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(storage_audit.db_client, "get_workflow_run", get_run)
    monkeypatch.setattr(storage_audit.db_client, "get_utterances_for_run", empty)
    monkeypatch.setattr(storage_audit.db_client, "get_call_recordings_for_run", empty)

    result = await storage_audit.audit_run_storage(7, organization_id=11)

    assert result["minio"]["status"] == "not_expected"
    assert result["minio"]["objects"] == []
