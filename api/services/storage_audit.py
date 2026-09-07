"""Read-only verification of workflow-run persistence and object storage."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from api.constants import ENABLE_AWS_S3, RECORD_CALLS
from api.db import db_client
from api.enums import StorageBackend
from api.services.storage import storage_fs


def _status(*, exists: bool, expected: bool) -> str:
    if exists:
        return "verified"
    return "missing" if expected else "not_expected"


async def _verify_object(object_key: str, expected_size: int | None) -> dict[str, Any]:
    metadata = await storage_fs.aget_file_metadata(object_key)
    if metadata is None:
        return {"key": object_key, "status": "missing"}

    checksum = None
    with tempfile.TemporaryDirectory(prefix="dograh-storage-audit-") as temp_dir:
        local_path = str(Path(temp_dir) / "object")
        if await storage_fs.adownload_file(object_key, local_path):
            digest = hashlib.sha256()
            data = await asyncio.to_thread(Path(local_path).read_bytes)
            digest.update(data)
            checksum = digest.hexdigest()

    return {
        "key": object_key,
        "status": "verified",
        "size_bytes": metadata.get("size"),
        "content_type": metadata.get("content_type"),
        "etag": metadata.get("etag"),
        "checksum_sha256": checksum,
        "size_matches_metadata": expected_size is None
        or metadata.get("size") == expected_size,
        "last_modified": metadata.get("modified_at"),
    }


async def audit_run_storage(
    run_id: int,
    *,
    organization_id: int | None = None,
) -> dict[str, Any]:
    """Verify one run's relational records and referenced local objects.

    This is intentionally read-only. It uses the normal DB client and storage
    abstraction and never creates tokens, uploads objects, or changes records.
    """
    run = await db_client.get_workflow_run(
        run_id,
        organization_id=organization_id,
    )
    if run is None:
        return {
            "run_id": run_id,
            "postgres": {
                "status": "missing",
                "run_exists": False,
                "transcript_exists": False,
                "recording_metadata_exists": False,
            },
            "minio": {"status": "not_expected", "configured": False, "objects": []},
            "aws": {
                "status": "configured_but_unused" if ENABLE_AWS_S3 else "not_configured"
            },
        }

    utterances = await db_client.get_utterances_for_run(run_id)
    recordings = await db_client.get_call_recordings_for_run(run_id)
    transcript_key = run.transcript_object_key or run.transcript_url
    transcript_expected = bool(transcript_key or run.full_transcript)

    objects: list[dict[str, Any]] = []
    if transcript_key:
        objects.append(
            {
                "type": "transcript",
                **await _verify_object(transcript_key, None),
            }
        )

    for recording in recordings:
        object_result = await _verify_object(recording.object_key, recording.size_bytes)
        if recording.checksum_sha256 and object_result.get("checksum_sha256"):
            object_result["checksum_matches_database"] = (
                recording.checksum_sha256 == object_result["checksum_sha256"]
            )
        objects.append(
            {
                "type": recording.track,
                "database_checksum_sha256": recording.checksum_sha256,
                **object_result,
            }
        )

    run_mode = getattr(run, "mode", None)
    expected_audio = bool(
        recordings
        or (
            RECORD_CALLS
            and run.is_completed
            and run_mode not in {"textchat", "chat"}
        )
    )
    transcript_status = _status(
        exists=bool(run.full_transcript or transcript_key), expected=transcript_expected
    )
    audio_found = any(
        item["type"] != "transcript" and item["status"] == "verified"
        for item in objects
    )
    minio_configured = run.storage_backend == StorageBackend.MINIO.value
    minio_status = "not_configured"
    if minio_configured:
        if not objects:
            minio_status = "pending" if expected_audio else "not_expected"
        elif expected_audio and not audio_found:
            minio_status = "missing"
        elif all(item["status"] == "verified" for item in objects):
            minio_status = "verified"
        else:
            minio_status = "missing" if objects else (
                "pending" if expected_audio else "not_expected"
            )

    return {
        "run_id": run_id,
        "postgres": {
            "status": "verified",
            "run_exists": True,
            "transcript_exists": bool(run.full_transcript or transcript_key),
            "transcript_status": transcript_status,
            "utterance_count": len(utterances),
            "recording_metadata_exists": bool(recordings),
            "recording_metadata_count": len(recordings),
            "storage_backend": run.storage_backend,
            "workflow_id": run.workflow_id,
            "organization_id": organization_id,
        },
        "minio": {
            "status": minio_status,
            "configured": minio_configured,
            "expected_audio": expected_audio,
            "audio_found": audio_found,
            "objects_found": sum(item["status"] == "verified" for item in objects),
            "objects": objects,
        },
        "aws": {
            "status": "configured_but_unused" if ENABLE_AWS_S3 else "not_configured"
        },
    }
