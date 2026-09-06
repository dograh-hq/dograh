"""Copy existing MinIO call artifacts to private AWS S3 with verification.

The command is a dry-run unless ``--execute`` is supplied. Run it from the
repository root after sourcing ``api/.env``::

    python -m scripts.migrate_minio_to_s3
    python -m scripts.migrate_minio_to_s3 --execute

Object keys are preserved. Each recording row is updated after its own verified
copy; a workflow-run backend is changed only after every artifact referenced by
that run has a byte-identical S3 copy. Source MinIO objects are never deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aioboto3
from loguru import logger
from minio import Minio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

from api.db.models import CallRecordingModel, WorkflowRunModel


@dataclass
class MigrationReport:
    dry_run: bool
    enumerated: int = 0
    referenced: int = 0
    unreferenced: int = 0
    would_copy: int = 0
    copied: int = 0
    already_verified: int = 0
    database_runs_updated: int = 0
    database_recordings_updated: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_keys(run: WorkflowRunModel) -> set[str]:
    keys = {
        value
        for value in (
            run.recording_object_key,
            run.recording_url,
            run.transcript_object_key,
            run.transcript_url,
        )
        if value and not str(value).lower().startswith(("http://", "https://"))
    }
    recordings = (run.extra or {}).get("recordings") or {}
    if isinstance(recordings, dict):
        for metadata in recordings.values():
            if isinstance(metadata, dict):
                key = metadata.get("storage_key")
                if key:
                    keys.add(str(key))
    return keys


def _update_extra_backends(run: WorkflowRunModel, migrated_keys: set[str]) -> None:
    extra = dict(run.extra or {})
    recordings = dict(extra.get("recordings") or {})
    changed = False
    for track, metadata in recordings.items():
        if (
            not isinstance(metadata, dict)
            or metadata.get("storage_key") not in migrated_keys
        ):
            continue
        recordings[track] = {**metadata, "storage_backend": "s3"}
        changed = True
    if changed:
        extra["recordings"] = recordings
        run.extra = extra
        flag_modified(run, "extra")


class MinioToS3Migrator:
    def __init__(self, *, execute: bool, prefix: str = ""):
        self.execute = execute
        self.prefix = prefix
        self.database_url = os.environ["DATABASE_URL"]
        self.minio_bucket = os.getenv("MINIO_BUCKET", "voice-audio")
        self.s3_bucket = os.getenv("AWS_RECORDINGS_BUCKET") or os.getenv("S3_BUCKET")
        if not self.s3_bucket:
            raise ValueError(
                "AWS_RECORDINGS_BUCKET (or existing S3_BUCKET) is required"
            )
        self.region = os.getenv("AWS_REGION") or os.getenv("S3_REGION", "eu-west-2")
        self.s3_endpoint_url = os.getenv("S3_ENDPOINT_URL") or None
        self.sse = os.getenv("S3_SERVER_SIDE_ENCRYPTION", "aws:kms") or None
        self.kms_key_id = os.getenv("S3_KMS_KEY_ID") or None
        self.minio = Minio(
            os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
        )
        self.s3_session = aioboto3.Session()

    def _s3_client_kwargs(self) -> dict[str, Any]:
        values: dict[str, Any] = {"region_name": self.region}
        if self.s3_endpoint_url:
            values["endpoint_url"] = self.s3_endpoint_url
        return values

    async def _download_s3_sha256(self, client, key: str) -> str | None:
        try:
            response = await client.get_object(Bucket=self.s3_bucket, Key=key)
        except Exception:
            return None
        digest = hashlib.sha256()
        body = response["Body"]
        while chunk := await body.read(1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()

    async def _copy_and_verify(self, client, key: str) -> tuple[bool, bool, str | None]:
        """Return (ok, copied, sha256)."""
        with tempfile.TemporaryDirectory(prefix="calmos-minio-s3-") as temp_dir:
            source_path = str(Path(temp_dir) / "source-object")
            try:
                await asyncio.to_thread(
                    self.minio.fget_object, self.minio_bucket, key, source_path
                )
                source_sha256 = await asyncio.to_thread(_sha256_file, source_path)
                destination_sha256 = await self._download_s3_sha256(client, key)
                if destination_sha256 == source_sha256:
                    return True, False, source_sha256
                if not self.execute:
                    return True, True, source_sha256

                extra_args: dict[str, Any] = {"Metadata": {"sha256": source_sha256}}
                if self.sse and (not self.s3_endpoint_url or self.sse != "aws:kms"):
                    extra_args["ServerSideEncryption"] = self.sse
                if self.kms_key_id and not self.s3_endpoint_url:
                    extra_args["SSEKMSKeyId"] = self.kms_key_id
                await client.upload_file(
                    source_path,
                    self.s3_bucket,
                    key,
                    ExtraArgs=extra_args,
                )
                destination_sha256 = await self._download_s3_sha256(client, key)
                return destination_sha256 == source_sha256, True, source_sha256
            except Exception as exc:
                return False, False, type(exc).__name__

    async def run(self) -> MigrationReport:
        report = MigrationReport(dry_run=not self.execute)
        engine = create_async_engine(self.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                runs = list((await session.execute(select(WorkflowRunModel))).scalars())
                recordings = list(
                    (await session.execute(select(CallRecordingModel))).scalars()
                )
                run_keys = {run.id: _artifact_keys(run) for run in runs}
                recording_keys = {item.object_key for item in recordings}
                referenced_keys = set().union(*run_keys.values(), recording_keys)

                objects = await asyncio.to_thread(
                    lambda: list(
                        self.minio.list_objects(
                            self.minio_bucket, prefix=self.prefix, recursive=True
                        )
                    )
                )
                report.enumerated = len(objects)
                report.referenced = sum(
                    1 for item in objects if item.object_name in referenced_keys
                )
                report.unreferenced = report.enumerated - report.referenced

                migrated: set[str] = set()
                checksums: dict[str, str] = {}
                async with self.s3_session.client(
                    "s3", **self._s3_client_kwargs()
                ) as client:
                    for item in objects:
                        key = item.object_name
                        if key not in referenced_keys:
                            continue
                        ok, copied, checksum = await self._copy_and_verify(client, key)
                        if not ok:
                            report.failures.append(
                                {
                                    "object_key": key,
                                    "error": checksum or "integrity_mismatch",
                                }
                            )
                            continue
                        migrated.add(key)
                        if checksum:
                            checksums[key] = checksum
                        if copied:
                            if self.execute:
                                report.copied += 1
                            else:
                                report.would_copy += 1
                        else:
                            report.already_verified += 1

                if not self.execute:
                    return report

                for recording in recordings:
                    if recording.object_key not in migrated:
                        continue
                    recording.storage_backend = "s3"
                    recording.checksum_sha256 = checksums.get(recording.object_key)
                    report.database_recordings_updated += 1

                for run in runs:
                    keys = run_keys[run.id]
                    if not keys or not keys.issubset(migrated):
                        continue
                    run.storage_backend = "s3"
                    _update_extra_backends(run, migrated)
                    report.database_runs_updated += 1

                await session.commit()
                return report
        finally:
            await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy referenced MinIO call artifacts to S3; dry-run by default"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform copies and update verified database references",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="optional MinIO object prefix to enumerate",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    report = await MinioToS3Migrator(execute=args.execute, prefix=args.prefix).run()
    print(json.dumps(report.__dict__, indent=2, sort_keys=True))
    return 1 if report.failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except (KeyError, ValueError) as exc:
        logger.error("Migration configuration error: {}", exc)
        raise SystemExit(2) from exc
