import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from api.services.filesystem import minio as minio_module
from api.services.filesystem import s3 as s3_module
from api.services.filesystem.s3 import S3FileSystem
from scripts.migrate_minio_to_s3 import MinioToS3Migrator, _artifact_keys


class _FakeMinioClient:
    instances: ClassVar[list] = []

    def __init__(self, endpoint, **kwargs):
        self.endpoint = endpoint
        self.kwargs = kwargs
        self.policy_set = False
        self.policy_deleted = False
        self.get_headers = None
        self.put_kwargs = None
        self.__class__.instances.append(self)

    def bucket_exists(self, _bucket):
        return True

    def set_bucket_policy(self, _bucket, _policy):
        self.policy_set = True

    def delete_bucket_policy(self, _bucket):
        self.policy_deleted = True

    def presigned_get_object(self, bucket, key, **kwargs):
        self.get_headers = kwargs.get("response_headers")
        return f"https://signed.invalid/{bucket}/{key}?signature=private"

    def presigned_put_object(self, bucket, key, **_kwargs):
        return f"https://signed.invalid/{bucket}/{key}?signature=private-put"

    def put_object(self, *args, **kwargs):
        self.put_kwargs = {"args": args, **kwargs}


def test_production_minio_removes_anonymous_policy_and_uses_presigned_urls(monkeypatch):
    _FakeMinioClient.instances = []
    monkeypatch.setattr(minio_module, "Minio", _FakeMinioClient)
    minio_module.MinioFileSystem(
        endpoint="minio:9000",
        access_key="test-access",
        secret_key="test-secret",
        bucket_name="private-recordings",
        public_endpoint="https://records.example.test",
        allow_anonymous_access=False,
    )
    assert _FakeMinioClient.instances[0].policy_deleted is True
    assert _FakeMinioClient.instances[0].policy_set is False
    assert all(instance.kwargs["region"] == "us-east-1" for instance in _FakeMinioClient.instances)


@pytest.mark.asyncio
async def test_private_minio_get_and_put_urls_are_signed(monkeypatch):
    _FakeMinioClient.instances = []
    monkeypatch.setattr(minio_module, "Minio", _FakeMinioClient)
    storage = minio_module.MinioFileSystem(
        endpoint="minio:9000",
        access_key="test-access",
        secret_key="test-secret",
        bucket_name="private-recordings",
        public_endpoint="https://records.example.test",
        allow_anonymous_access=False,
    )
    get_url = await storage.aget_signed_url("recordings/call.wav", expiration=300)
    put_url = await storage.aget_presigned_put_url("campaigns/source.csv")
    assert "signature=private" in get_url
    assert "signature=private-put" in put_url
    assert all(instance.kwargs["region"] == "us-east-1" for instance in _FakeMinioClient.instances)


@pytest.mark.asyncio
async def test_minio_recordings_are_typed_and_signed_for_inline_preview(monkeypatch):
    _FakeMinioClient.instances = []
    monkeypatch.setattr(minio_module, "Minio", _FakeMinioClient)
    storage = minio_module.MinioFileSystem(
        endpoint="minio:9000",
        access_key="test-access",
        secret_key="test-secret",
        bucket_name="private-recordings",
        public_endpoint="https://records.example.test",
        allow_anonymous_access=False,
    )

    assert await storage.acreate_file_from_bytes("recordings/call.wav", b"audio")
    assert _FakeMinioClient.instances[0].put_kwargs["content_type"] == "audio/wav"

    await storage.aget_signed_url("recordings/call.wav", force_inline=True)
    assert _FakeMinioClient.instances[1].get_headers == {
        "response-content-disposition": "inline",
        "response-content-type": "audio/wav",
    }

    await storage.aget_signed_url("recordings/call.wav")
    assert _FakeMinioClient.instances[1].get_headers == {
        "response-content-disposition": "attachment",
        "response-content-type": "audio/wav",
    }


def test_minio_migration_discovers_all_existing_run_artifact_keys():
    run = SimpleNamespace(
        recording_object_key="recordings/call.wav",
        recording_url="recordings/call.wav",
        transcript_object_key="transcripts/call.txt",
        transcript_url="https://legacy.invalid/transcript.txt",
        extra={
            "recordings": {
                "user": {"storage_key": "recordings/user.wav"},
                "bot": {"storage_key": "recordings/assistant.wav"},
            }
        },
    )
    assert _artifact_keys(run) == {
        "recordings/call.wav",
        "recordings/user.wav",
        "recordings/assistant.wav",
        "transcripts/call.txt",
    }


class _MigrationSource:
    def __init__(self, data: bytes):
        self.data = data

    def fget_object(self, _bucket, _key, path):
        with open(path, "wb") as handle:
            handle.write(self.data)


class _Body:
    def __init__(self, data: bytes):
        self._stream = io.BytesIO(data)

    async def read(self, size):
        return self._stream.read(size)


class _MigrationDestination:
    def __init__(self):
        self.data = None
        self.uploads = 0

    async def get_object(self, **_kwargs):
        if self.data is None:
            raise RuntimeError("not found")
        return {"Body": _Body(self.data)}

    async def upload_file(self, path, _bucket, _key, ExtraArgs):
        assert "ACL" not in ExtraArgs
        self.data = await asyncio.to_thread(Path(path).read_bytes)
        self.uploads += 1


class _TransientMigrationDestination(_MigrationDestination):
    def __init__(self):
        super().__init__()
        self.attempts = 0

    async def upload_file(self, path, bucket, key, ExtraArgs):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("temporary destination outage")
        await super().upload_file(path, bucket, key, ExtraArgs)


class _CorruptingMigrationDestination(_MigrationDestination):
    def __init__(self):
        super().__init__()
        self.attempts = 0

    async def upload_file(self, _path, _bucket, _key, ExtraArgs):
        assert "ACL" not in ExtraArgs
        self.attempts += 1
        self.data = b"corrupt-destination-data"


@pytest.mark.asyncio
async def test_minio_to_s3_copy_is_verified_and_repeatable():
    migrator = object.__new__(MinioToS3Migrator)
    migrator.execute = True
    migrator.minio_bucket = "source"
    migrator.s3_bucket = "destination"
    migrator.s3_endpoint_url = "http://s3-compatible.test"
    migrator.sse = None
    migrator.kms_key_id = None
    migrator.minio = _MigrationSource(b"durable-call-audio")
    destination = _MigrationDestination()

    ok, copied, checksum = await migrator._copy_and_verify(
        destination, "recordings/call.wav"
    )
    assert ok is True
    assert copied is True
    assert len(checksum) == 64
    assert destination.uploads == 1

    ok, copied, checksum_again = await migrator._copy_and_verify(
        destination, "recordings/call.wav"
    )
    assert ok is True
    assert copied is False
    assert checksum_again == checksum
    assert destination.uploads == 1


@pytest.mark.asyncio
async def test_minio_to_s3_dry_run_does_not_upload():
    migrator = object.__new__(MinioToS3Migrator)
    migrator.execute = False
    migrator.minio_bucket = "source"
    migrator.s3_bucket = "destination"
    migrator.s3_endpoint_url = "http://s3-compatible.test"
    migrator.sse = None
    migrator.kms_key_id = None
    migrator.minio = _MigrationSource(b"durable-call-audio")
    destination = _MigrationDestination()

    ok, would_copy, checksum = await migrator._copy_and_verify(
        destination, "recordings/call.wav"
    )
    assert ok is True
    assert would_copy is True
    assert len(checksum) == 64
    assert destination.uploads == 0


@pytest.mark.asyncio
async def test_minio_to_s3_copy_retries_transient_failures(monkeypatch):
    migrator = object.__new__(MinioToS3Migrator)
    migrator.execute = True
    migrator.minio_bucket = "source"
    migrator.s3_bucket = "destination"
    migrator.s3_endpoint_url = "http://s3-compatible.test"
    migrator.sse = None
    migrator.kms_key_id = None
    migrator.minio = _MigrationSource(b"durable-call-audio")
    destination = _TransientMigrationDestination()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("scripts.migrate_minio_to_s3.asyncio.sleep", no_sleep)
    ok, copied, checksum = await migrator._copy_and_verify(
        destination, "recordings/call.wav"
    )

    assert ok is True
    assert copied is True
    assert len(checksum) == 64
    assert destination.attempts == 2
    assert destination.uploads == 1


@pytest.mark.asyncio
async def test_minio_to_s3_reports_integrity_failure_after_retries(monkeypatch):
    migrator = object.__new__(MinioToS3Migrator)
    migrator.execute = True
    migrator.minio_bucket = "source"
    migrator.s3_bucket = "destination"
    migrator.s3_endpoint_url = "http://s3-compatible.test"
    migrator.sse = None
    migrator.kms_key_id = None
    migrator.minio = _MigrationSource(b"durable-call-audio")
    destination = _CorruptingMigrationDestination()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("scripts.migrate_minio_to_s3.asyncio.sleep", no_sleep)
    ok, copied, error = await migrator._copy_and_verify(
        destination, "recordings/call.wav"
    )

    assert ok is False
    assert copied is True
    assert error == "integrity_mismatch"
    assert destination.attempts == 3


class _S3ClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return False


class _PrivateS3Client:
    def __init__(self):
        self.put_kwargs = None

    async def put_object(self, **kwargs):
        self.put_kwargs = kwargs


@pytest.mark.asyncio
async def test_s3_upload_never_requests_public_acl():
    client = _PrivateS3Client()
    storage = S3FileSystem(bucket_name="private-recordings", region_name="eu-west-2")
    storage.session = SimpleNamespace(
        client=lambda *_args, **_kwargs: _S3ClientContext(client)
    )
    assert await storage.acreate_file_from_bytes("recordings/call.wav", b"audio")
    assert client.put_kwargs["Bucket"] == "private-recordings"
    assert client.put_kwargs["Key"] == "recordings/call.wav"
    assert client.put_kwargs["ContentType"] == "audio/wav"
    assert "ACL" not in client.put_kwargs


@pytest.mark.asyncio
async def test_s3_kms_without_customer_key_uses_bucket_default_encryption(monkeypatch):
    client = _PrivateS3Client()
    storage = S3FileSystem(bucket_name="private-recordings", region_name="eu-west-2")
    storage.session = SimpleNamespace(
        client=lambda *_args, **_kwargs: _S3ClientContext(client)
    )
    monkeypatch.setattr(s3_module, "S3_SERVER_SIDE_ENCRYPTION", "aws:kms")
    monkeypatch.setattr(s3_module, "S3_KMS_KEY_ID", None)

    assert await storage.acreate_file_from_bytes("recordings/call.wav", b"audio")
    # The bucket's default encryption is authoritative when no CMK is named;
    # forcing aws:kms here would require KMS permissions unnecessarily.
    assert "ServerSideEncryption" not in client.put_kwargs
    assert "SSEKMSKeyId" not in client.put_kwargs
