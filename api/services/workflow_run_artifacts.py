"""Upload end-of-call artifacts and report their actual persistence results.

Called from the pipeline process itself, straight from the in-memory call
buffers, so no local file has to cross a process/host boundary. Uploads happen
before the workflow-completion job is enqueued so QA and webhooks see the
artifacts in storage.
"""

import asyncio
import hashlib
import io
import json
import wave
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.constants import RECORD_CALLS
from api.db import db_client
from api.services.s3_secondary_replication import schedule_s3_replication
from api.services.storage import get_current_storage_backend, storage_fs


def _recording_metadata(
    storage_key: str,
    storage_backend: str,
    track: str,
    data: bytes | None = None,
) -> dict:
    duration = None
    if data:
        try:
            with wave.open(io.BytesIO(data), "rb") as wav_file:
                duration = wav_file.getnframes() / wav_file.getframerate()
        except (EOFError, wave.Error, ZeroDivisionError):
            pass
    return {
        "storage_key": storage_key,
        "storage_backend": storage_backend,
        "format": "wav",
        "track": track,
        "duration_seconds": duration,
        "size_bytes": len(data) if data is not None else None,
        "checksum_sha256": hashlib.sha256(data).hexdigest()
        if data is not None
        else None,
    }


async def _upload_bytes(
    workflow_run_id: int,
    data: bytes,
    storage_key: str,
    label: str,
) -> bool:
    for attempt in range(3):
        try:
            if await storage_fs.acreate_file_from_bytes(storage_key, data):
                logger.info("Uploaded {} for workflow run {}", label, workflow_run_id)
                return True
        except Exception as exc:  # noqa: BLE001 - artifact writes are non-critical
            logger.warning(
                "Storage upload attempt {} failed for workflow run {} ({}) error_class={}",
                attempt + 1,
                workflow_run_id,
                label,
                type(exc).__name__,
            )
        if attempt < 2:
            await asyncio.sleep(0.25 * (2**attempt))
    logger.error(
        "Storage upload failed after retries for workflow run {} ({})",
        workflow_run_id,
        label,
    )
    return False


async def _persist_recording_metadata(
    workflow_run_id: int,
    metadata: dict,
    *,
    update_run: bool = False,
) -> bool:
    """Persist one artifact's metadata without stopping other artifact work."""
    saved = True
    if update_run:
        try:
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                recording_url=metadata["storage_key"],
                storage_backend=metadata["storage_backend"],
                recording_object_key=metadata["storage_key"],
                recording_duration_seconds=metadata["duration_seconds"],
                recording_format=metadata["format"],
                recording_size_bytes=metadata["size_bytes"],
            )
        except Exception:  # noqa: BLE001 - artifact writes are non-critical
            saved = False
            logger.warning(
                "Recording run metadata write failed for workflow run {}",
                workflow_run_id,
            )
    try:
        await db_client.upsert_call_recording(
            agent_run_id=workflow_run_id,
            storage_backend=metadata["storage_backend"],
            object_key=metadata["storage_key"],
            duration_seconds=metadata["duration_seconds"],
            format=metadata["format"],
            size_bytes=metadata["size_bytes"],
            checksum_sha256=metadata["checksum_sha256"],
            track=metadata["track"],
        )
    except Exception:  # noqa: BLE001 - artifact writes are non-critical
        saved = False
        logger.warning(
            "Recording metadata write failed for workflow run {} ({})",
            workflow_run_id,
            metadata["track"],
        )
    return saved


async def _persist_run_fields(workflow_run_id: int, **fields) -> bool:
    try:
        await db_client.update_workflow_run(run_id=workflow_run_id, **fields)
        return True
    except Exception:  # noqa: BLE001 - artifact writes are non-critical
        logger.warning(
            "Artifact metadata write failed for workflow run {}", workflow_run_id
        )
        return False


def _storage_bucket() -> str | None:
    """Return only the configured bucket name, never storage credentials."""
    bucket = getattr(storage_fs, "bucket_name", None)
    return str(bucket) if bucket else None


def _overall_status(audit: dict[str, Any]) -> str:
    statuses = [audit["postgres"]["status"]]
    for component in (audit["transcript"], audit["recordings"]):
        if component["status"] != "not_expected":
            statuses.append(component["status"])
    if all(status == "success" for status in statuses):
        return "success"
    if any(status in {"success", "partial"} for status in statuses):
        return "partial"
    if any(status == "unknown" for status in statuses):
        return "unknown"
    return "failed"


def _log_storage_audit(audit: dict[str, Any]) -> None:
    """Emit a safe, searchable finalization event without call-content data."""
    try:
        logger.bind(
            event="storage_audit.finalized",
            storage_audit=True,
            **audit,
        ).info(
            "storage_audit.finalized {}",
            json.dumps(
                {"event": "storage_audit.finalized", **audit},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    except Exception:  # noqa: BLE001 - logging must not break finalization
        # Logging must never turn a completed call into a failed call.
        logger.debug("Unable to emit storage audit for workflow run {}", audit["run_id"])


async def upload_workflow_run_artifacts(
    workflow_run_id: int,
    *,
    mixed_audio_wav: bytes | None = None,
    user_audio_wav: bytes | None = None,
    bot_audio_wav: bytes | None = None,
    transcript_text: str | None = None,
) -> dict[str, Any]:
    """Upload call artifacts, persist metadata, and emit a finalization audit.

    Every status in the returned/logged audit is based on an actual upload or
    database operation. The audit is defensive and never raises into the
    completed-call path.
    """
    storage_backend = get_current_storage_backend()
    backend_name = storage_backend.value
    bucket = _storage_bucket()
    try:
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    except Exception:  # noqa: BLE001 - audit remains best-effort
        workflow_run = None
        logger.warning(
            "Unable to load workflow run {} before artifact finalization",
            workflow_run_id,
        )

    workflow_id = getattr(workflow_run, "workflow_id", None)
    finalization_status = getattr(workflow_run, "state", None)
    if hasattr(finalization_status, "value"):
        finalization_status = finalization_status.value
    audit: dict[str, Any] = {
        "run_id": workflow_run_id,
        "workflow_id": workflow_id,
        "storage_backend": backend_name,
        "postgres": {
            "status": "success" if workflow_run else "unknown",
            "run_row_id": getattr(workflow_run, "id", workflow_run_id),
            "run_row_exists": workflow_run is not None,
            "transcript_saved": False,
            "recording_metadata_saved": True,
        },
        "transcript": {
            "status": "not_expected",
            "backend": backend_name,
            "bucket": bucket,
            "object_key": None,
            "postgres_saved": False,
        },
        "recordings": {
            "status": "not_expected",
            "backend": backend_name,
            "bucket": bucket,
            "objects": [],
        },
        "artifact_count": 0,
        "finalization_status": finalization_status
        or ("completed" if getattr(workflow_run, "is_completed", False) else "finalizing"),
    }

    call_id = str(getattr(workflow_run, "call_id", None) or workflow_run_id)
    service_user_id = str(getattr(workflow_run, "service_user_id", None) or "anonymous")
    started_at = getattr(workflow_run, "started_at", None) or datetime.now(UTC)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    artifact_root = (
        f"{started_at.year:04d}/{started_at.month:02d}/{service_user_id}/{call_id}"
    )
    if not RECORD_CALLS:
        mixed_audio_wav = user_audio_wav = bot_audio_wav = None

    recordings_metadata: dict[str, dict] = {}
    expected_recordings = 0
    recording_db_writes_ok = True

    async def save_recording(track: str, storage_key: str, data: bytes) -> None:
        nonlocal expected_recordings, recording_db_writes_ok
        expected_recordings += 1
        recording = {
            "type": track,
            "backend": backend_name,
            "bucket": bucket,
            "object_key": storage_key,
            "size_bytes": len(data),
            "upload_status": "failed",
            "metadata_status": "not_expected",
            "status": "failed",
        }
        if await _upload_bytes(workflow_run_id, data, storage_key, f"{track} audio"):
            recording["upload_status"] = "success"
            metadata = _recording_metadata(storage_key, backend_name, track, data)
            recordings_metadata[track] = metadata
            metadata_saved = await _persist_recording_metadata(
                workflow_run_id,
                metadata,
                update_run=track == "mixed",
            )
            recording["metadata_status"] = "success" if metadata_saved else "failed"
            recording["status"] = "success" if metadata_saved else "partial"
            recording_db_writes_ok = recording_db_writes_ok and metadata_saved
        audit["recordings"]["objects"].append(recording)

    if mixed_audio_wav:
        recording_url = f"recordings/{artifact_root}/call.wav"
        logger.info(
            "Uploading mixed audio to {} - workflow_run_id: {}",
            storage_backend.name,
            workflow_run_id,
        )
        await save_recording("mixed", recording_url, mixed_audio_wav)

    if user_audio_wav:
        user_recording_url = f"recordings/{artifact_root}/user.wav"
        logger.info(
            "Uploading user audio to {} - workflow_run_id: {}",
            storage_backend.name,
            workflow_run_id,
        )
        await save_recording("user", user_recording_url, user_audio_wav)

    if bot_audio_wav:
        # Keep the legacy database/UI track label ("bot") while using the
        # product-facing object name for new CALMOS calls.
        bot_recording_url = f"recordings/{artifact_root}/assistant.wav"
        logger.info(
            "Uploading bot audio to {} - workflow_run_id: {}",
            storage_backend.name,
            workflow_run_id,
        )
        await save_recording("bot", bot_recording_url, bot_audio_wav)

    if recordings_metadata:
        extra_saved = await _persist_run_fields(
            workflow_run_id,
            storage_backend=backend_name,
            extra={"recordings": recordings_metadata},
        )
        recording_db_writes_ok = recording_db_writes_ok and extra_saved

    if expected_recordings:
        objects = audit["recordings"]["objects"]
        if all(item["status"] == "success" for item in objects):
            audit["recordings"]["status"] = "success"
        elif any(item["status"] in {"success", "partial"} for item in objects):
            audit["recordings"]["status"] = "partial"
        else:
            audit["recordings"]["status"] = "failed"
        audit["postgres"]["recording_metadata_saved"] = recording_db_writes_ok

    if transcript_text:
        transcript_url = f"transcripts/{artifact_root}/transcript.txt"
        audit["transcript"].update(
            {"status": "failed", "object_key": transcript_url}
        )
        logger.info(
            "Uploading transcript to {} - workflow_run_id: {}",
            storage_backend.name,
            workflow_run_id,
        )
        if await _upload_bytes(
            workflow_run_id,
            transcript_text.encode("utf-8"),
            transcript_url,
            "transcript",
        ):
            transcript_saved = await _persist_run_fields(
                workflow_run_id,
                transcript_url=transcript_url,
                transcript_object_key=transcript_url,
                storage_backend=backend_name,
                full_transcript=transcript_text,
            )
            audit["transcript"].update(
                {
                    "status": "success" if transcript_saved else "partial",
                    "postgres_saved": transcript_saved,
                }
            )
            audit["postgres"]["transcript_saved"] = transcript_saved

    audit["artifact_count"] = len(audit["recordings"]["objects"]) + int(
        audit["transcript"]["status"] != "not_expected"
    )
    if audit["postgres"]["status"] == "success" and (
        not audit["postgres"]["recording_metadata_saved"]
        or (transcript_text and not audit["postgres"]["transcript_saved"])
    ):
        audit["postgres"]["status"] = "failed"
    audit["postgres_saved"] = audit["postgres"]["status"] == "success"
    audit["transcript_saved"] = audit["transcript"]["status"] == "success"
    audit["recording_saved"] = audit["recordings"]["status"] == "success"
    audit["transcript_storage_backend"] = backend_name
    audit["transcript_object_key"] = audit["transcript"]["object_key"]
    audit["recording_storage_backend"] = backend_name
    audit["recording_object_keys"] = [
        item["object_key"] for item in audit["recordings"]["objects"]
    ]
    audit["overall_status"] = _overall_status(audit)
    # MinIO/Postgres are authoritative. S3 is only scheduled after the
    # primary writes above have completed, and scheduling is isolated from the
    # call finalization result.
    try:
        audit["s3"] = await schedule_s3_replication(workflow_run_id, audit)
    except Exception as exc:  # noqa: BLE001 - secondary scheduling is isolated
        audit["s3"] = {
            "role": "secondary",
            "backend": "s3",
            "status": "failed",
            "error_class": type(exc).__name__,
        }
        logger.warning(
            "S3 secondary scheduling failed for workflow run {} ({})",
            workflow_run_id,
            type(exc).__name__,
        )
    audit["overall_primary_status"] = audit["overall_status"]
    _log_storage_audit(audit)

    logger.info(
        "workflow_run_storage_saved run_id={} postgres_saved={} "
        "transcript_saved={} recording_metadata_saved={} minio_status={} "
        "minio_object_count={} storage_backend={} completion_time={}",
        workflow_run_id,
        audit["postgres_saved"],
        audit["transcript_saved"],
        audit["postgres"]["recording_metadata_saved"],
        audit["recordings"]["status"],
        audit["artifact_count"],
        backend_name,
        datetime.now(UTC).isoformat(),
    )
    return audit
