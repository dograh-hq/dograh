import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import storage_audit, workflow_run_artifacts


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


def _artifact_run():
    return SimpleNamespace(
        id=88,
        workflow_id=12,
        call_id="call-88",
        service_user_id="service-user-88",
        started_at=datetime(2026, 9, 7, tzinfo=UTC),
        state="completed",
        is_completed=True,
    )


@pytest.mark.asyncio
async def test_finalization_audit_reports_actual_minio_and_postgres_results(monkeypatch):
    events = []

    class _UploadingStorage:
        bucket_name = "voice-audio"

        async def acreate_file_from_bytes(self, key, data):
            return True

    monkeypatch.setattr(workflow_run_artifacts, "storage_fs", _UploadingStorage())
    monkeypatch.setattr(
        workflow_run_artifacts,
        "get_current_storage_backend",
        lambda: SimpleNamespace(value="minio", name="MINIO"),
    )
    monkeypatch.setattr(workflow_run_artifacts, "RECORD_CALLS", True)
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=_artifact_run()),
    )
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "update_workflow_run",
        AsyncMock(),
    )
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "upsert_call_recording",
        AsyncMock(),
    )
    monkeypatch.setattr(
        workflow_run_artifacts,
        "_log_storage_audit",
        events.append,
    )

    audit = await workflow_run_artifacts.upload_workflow_run_artifacts(
        88,
        mixed_audio_wav=b"call-audio",
        user_audio_wav=b"user-audio",
        bot_audio_wav=b"bot-audio",
        transcript_text="secret transcript that must not be logged",
    )

    assert audit["overall_status"] == "success"
    assert audit["postgres"]["run_row_id"] == 88
    assert audit["postgres_saved"] is True
    assert audit["transcript"]["status"] == "success"
    assert audit["transcript"]["object_key"].endswith("/transcript.txt")
    assert audit["recordings"]["bucket"] == "voice-audio"
    assert {item["type"] for item in audit["recordings"]["objects"]} == {
        "mixed",
        "user",
        "bot",
    }
    assert audit["artifact_count"] == 4
    assert audit["s3"]["status"] == "disabled"
    assert audit["overall_primary_status"] == "success"
    assert "secret transcript" not in repr(events[0])


@pytest.mark.asyncio
async def test_finalization_audit_reports_partial_storage_failure(monkeypatch):
    events = []

    class _PartiallyFailingStorage:
        bucket_name = "voice-audio"

        async def acreate_file_from_bytes(self, key, data):
            return not key.endswith("assistant.wav")

    monkeypatch.setattr(workflow_run_artifacts, "storage_fs", _PartiallyFailingStorage())
    monkeypatch.setattr(
        workflow_run_artifacts,
        "get_current_storage_backend",
        lambda: SimpleNamespace(value="minio", name="MINIO"),
    )
    monkeypatch.setattr(workflow_run_artifacts, "RECORD_CALLS", True)
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=_artifact_run()),
    )
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "update_workflow_run",
        AsyncMock(),
    )
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "upsert_call_recording",
        AsyncMock(),
    )
    monkeypatch.setattr(
        workflow_run_artifacts,
        "_log_storage_audit",
        events.append,
    )

    audit = await workflow_run_artifacts.upload_workflow_run_artifacts(
        88,
        mixed_audio_wav=b"call-audio",
        bot_audio_wav=b"bot-audio",
    )

    assert audit["recordings"]["status"] == "partial"
    assert audit["overall_status"] == "partial"
    assert any(
        item["object_key"].endswith("assistant.wav")
        and item["status"] == "failed"
        for item in events[0]["recordings"]["objects"]
    )


@pytest.mark.asyncio
async def test_finalization_audit_marks_recording_not_expected(monkeypatch):
    events = []
    monkeypatch.setattr(
        workflow_run_artifacts,
        "get_current_storage_backend",
        lambda: SimpleNamespace(value="minio", name="MINIO"),
    )
    monkeypatch.setattr(workflow_run_artifacts, "RECORD_CALLS", True)
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=_artifact_run()),
    )
    monkeypatch.setattr(workflow_run_artifacts, "_log_storage_audit", events.append)

    audit = await workflow_run_artifacts.upload_workflow_run_artifacts(88)

    assert audit["recordings"]["status"] == "not_expected"
    assert audit["overall_status"] == "success"
    assert events[0]["artifact_count"] == 0


@pytest.mark.asyncio
async def test_s3_schedule_failure_does_not_fail_primary_finalization(monkeypatch):
    class _UploadingStorage:
        bucket_name = "voice-audio"

        async def acreate_file_from_bytes(self, key, data):
            return True

    monkeypatch.setattr(workflow_run_artifacts, "storage_fs", _UploadingStorage())
    monkeypatch.setattr(
        workflow_run_artifacts,
        "get_current_storage_backend",
        lambda: SimpleNamespace(value="minio", name="MINIO"),
    )
    monkeypatch.setattr(workflow_run_artifacts, "RECORD_CALLS", True)
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=_artifact_run()),
    )
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "update_workflow_run",
        AsyncMock(),
    )
    monkeypatch.setattr(
        workflow_run_artifacts.db_client,
        "upsert_call_recording",
        AsyncMock(),
    )
    async def failed_schedule(*args, **kwargs):
        return {"role": "secondary", "status": "failed"}

    monkeypatch.setattr(
        workflow_run_artifacts,
        "schedule_s3_replication",
        failed_schedule,
    )

    audit = await workflow_run_artifacts.upload_workflow_run_artifacts(
        88,
        mixed_audio_wav=b"call-audio",
    )

    assert audit["overall_status"] == "success"
    assert audit["overall_primary_status"] == "success"
    assert audit["s3"]["status"] == "failed"
