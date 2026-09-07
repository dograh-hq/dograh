import asyncio
from pathlib import Path
from typing import ClassVar

import pytest
from botocore.exceptions import ClientError

from api.services import s3_secondary_replication as replication


def _refs():
    return [
        {
            "type": "call",
            "object_key": "recordings/2026/09/service/call-1/call.wav",
            "size_bytes": 4,
        },
        {
            "type": "transcript",
            "object_key": "transcripts/2026/09/service/call-1/transcript.txt",
            "size_bytes": 4,
        },
    ]


class _Primary:
    objects: ClassVar = {
        "recordings/2026/09/service/call-1/call.wav": b"WAVE",
        "transcripts/2026/09/service/call-1/transcript.txt": b"TEXT",
    }

    async def adownload_file(self, source_key, local_path):
        data = self.objects.get(source_key)
        if data is None:
            return False
        await asyncio.to_thread(Path(local_path).write_bytes, data)
        return True


class _Secondary:
    def __init__(self, failures=0, error=None):
        self.failures = failures
        self.error = error or RuntimeError("S3UploadFailed")
        self.uploads = []

    async def aupload_file_checked(self, local_path, destination_key):
        if self.failures:
            self.failures -= 1
            raise self.error
        self.uploads.append((destination_key, await asyncio.to_thread(Path(local_path).read_bytes)))


@pytest.mark.asyncio
async def test_disabled_secondary_does_not_enqueue_or_initialize_s3(monkeypatch):
    monkeypatch.setattr(replication, "ENABLE_AWS_S3_SECONDARY", False)
    monkeypatch.setattr(
        replication,
        "S3FileSystem",
        lambda **kwargs: pytest.fail("S3 must not be initialized when disabled"),
    )

    result = await replication.replicate_workflow_run_artifacts_to_s3(
        None, 1, _refs()
    )

    assert result["status"] == "disabled"


@pytest.mark.asyncio
async def test_schedule_preserves_primary_keys_and_reports_pending(monkeypatch):
    monkeypatch.setattr(replication, "ENABLE_AWS_S3_SECONDARY", True)
    monkeypatch.setattr(replication, "AWS_RECORDINGS_BUCKET", "private-test-bucket")
    monkeypatch.setattr(replication, "AWS_S3_PREFIX", "calmos")
    monkeypatch.setattr(
        replication,
        "get_current_storage_backend",
        lambda: replication.StorageBackend.MINIO,
    )
    calls = []

    async def enqueue(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(
        "api.tasks.arq.enqueue_job",
        enqueue,
    )

    result = await replication.schedule_s3_replication(
        88,
        {
            "recordings": {"objects": [{**_refs()[0], "status": "success"}]},
            "transcript": {"status": "not_expected"},
        },
    )

    assert result["status"] == "pending"
    assert result["objects"][0]["source_object_key"].startswith("recordings/")
    assert result["objects"][0]["object_key"].startswith("calmos/recordings/")
    assert calls[0][0][0] == replication.FunctionNames.REPLICATE_WORKFLOW_RUN_ARTIFACTS_S3
    assert calls[0][0][1] == 88


@pytest.mark.asyncio
async def test_successful_copy_preserves_exact_bytes_and_emits_event(monkeypatch):
    primary = _Primary()
    secondary = _Secondary()
    events = []

    def record_event(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(replication, "storage_fs", primary)
    monkeypatch.setattr(replication, "AWS_RECORDINGS_BUCKET", "private-test-bucket")
    monkeypatch.setattr(replication, "AWS_S3_PREFIX", "")
    monkeypatch.setattr(replication, "_log_replication_event", record_event)

    result = await replication._replicate_one(secondary, 88, _refs()[0])

    assert result["status"] == "success"
    assert secondary.uploads == [(_refs()[0]["object_key"], b"WAVE")]
    assert events[0]["status"] == "success"
    assert events[0]["object_key"] == _refs()[0]["object_key"]


@pytest.mark.asyncio
async def test_retry_then_success_does_not_change_primary(monkeypatch):
    primary = _Primary()
    secondary = _Secondary(failures=1, error=TimeoutError())
    events = []
    sleeps = []

    def record_event(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(replication, "storage_fs", primary)
    monkeypatch.setattr(replication, "AWS_RECORDINGS_BUCKET", "private-test-bucket")
    monkeypatch.setattr(replication, "_log_replication_event", record_event)
    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(replication.asyncio, "sleep", record_sleep)

    result = await replication._replicate_one(secondary, 88, _refs()[0])

    assert result["status"] == "success"
    assert result["attempt"] == 2
    assert secondary.uploads == [(_refs()[0]["object_key"], b"WAVE")]
    assert sleeps == [0.25]
    assert primary.objects[_refs()[0]["object_key"]] == b"WAVE"
    assert events[-1]["status"] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [PermissionError(), FileNotFoundError(), TimeoutError()])
async def test_retry_exhaustion_isolated_and_classified(monkeypatch, error):
    primary = _Primary()
    secondary = _Secondary(failures=10, error=error)
    events = []

    def record_event(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(replication, "storage_fs", primary)
    monkeypatch.setattr(replication, "AWS_RECORDINGS_BUCKET", "private-test-bucket")
    monkeypatch.setattr(replication, "_log_replication_event", record_event)
    async def skip_sleep(_delay):
        return None

    monkeypatch.setattr(replication.asyncio, "sleep", skip_sleep)

    result = await replication._replicate_one(secondary, 88, _refs()[0])

    assert result["status"] == "failed"
    assert result["attempt"] == 3
    assert result["error_class"] == type(error).__name__
    assert events[-1]["status"] == "failed"
    assert primary.objects[_refs()[0]["object_key"]] == b"WAVE"


@pytest.mark.asyncio
async def test_missing_bucket_is_not_configured(monkeypatch):
    monkeypatch.setattr(replication, "ENABLE_AWS_S3_SECONDARY", True)
    monkeypatch.setattr(replication, "AWS_RECORDINGS_BUCKET", None)
    monkeypatch.setattr(
        replication,
        "get_current_storage_backend",
        lambda: replication.StorageBackend.MINIO,
    )

    result = replication.secondary_status(_refs())

    assert result["status"] == "not_configured"


@pytest.mark.asyncio
async def test_provider_access_denied_code_is_safe_and_specific(monkeypatch):
    primary = _Primary()
    secondary = _Secondary(
        failures=3,
        error=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}},
            "PutObject",
        ),
    )
    events = []

    monkeypatch.setattr(replication, "storage_fs", primary)
    monkeypatch.setattr(replication, "AWS_RECORDINGS_BUCKET", "private-test-bucket")

    def record_event(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr(replication, "_log_replication_event", record_event)

    async def skip_sleep(_delay):
        return None

    monkeypatch.setattr(replication.asyncio, "sleep", skip_sleep)

    result = await replication._replicate_one(secondary, 88, _refs()[0])

    assert result["status"] == "failed"
    assert result["error_class"] == "AccessDenied"
    assert events[-1]["error_class"] == "AccessDenied"
