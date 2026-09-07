"""Best-effort asynchronous replication from the MinIO primary to S3.

Stage C deliberately keeps the primary finalization path independent from S3.
The finalizer records the primary result, then enqueues one ARQ job. The job
downloads the already-persisted MinIO bytes and uploads those exact bytes to
the secondary bucket with bounded retries.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from loguru import logger

from api.constants import (
    AWS_RECORDINGS_BUCKET,
    AWS_REGION,
    AWS_S3_PREFIX,
    ENABLE_AWS_S3_SECONDARY,
)
from api.enums import StorageBackend
from api.services.filesystem.s3 import S3FileSystem
from api.services.storage import get_current_storage_backend, storage_fs
from api.tasks.function_names import FunctionNames

MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.25, 0.5)


def _destination_key(object_key: str) -> str:
    if AWS_S3_PREFIX:
        return f"{AWS_S3_PREFIX}/{object_key}"
    return object_key


def _artifact_refs(audit: dict[str, Any]) -> list[dict[str, Any]]:
    """Select only objects confirmed successful in the primary store."""
    refs: list[dict[str, Any]] = []
    for item in audit.get("recordings", {}).get("objects", []):
        if item.get("status") == "success" and item.get("object_key"):
            refs.append(
                {
                    "type": item.get("type"),
                    "object_key": item["object_key"],
                    "size_bytes": item.get("size_bytes"),
                }
            )
    transcript = audit.get("transcript", {})
    if transcript.get("status") == "success" and transcript.get("object_key"):
        refs.append(
            {
                "type": "transcript",
                "object_key": transcript["object_key"],
                "size_bytes": None,
            }
        )
    return refs


def secondary_status(artifact_refs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return safe scheduling state without initializing an AWS client."""
    objects = [
        {
            "type": item.get("type"),
            "source_object_key": item["object_key"],
            "object_key": _destination_key(item["object_key"]),
            "size_bytes": item.get("size_bytes"),
            "status": "pending",
        }
        for item in artifact_refs
    ]
    result: dict[str, Any] = {
        "role": "secondary",
        "backend": "s3",
        "bucket": AWS_RECORDINGS_BUCKET,
        "prefix": AWS_S3_PREFIX or None,
        "objects": objects,
    }
    if not ENABLE_AWS_S3_SECONDARY:
        result["status"] = "disabled"
    elif not AWS_RECORDINGS_BUCKET:
        result["status"] = "not_configured"
    elif get_current_storage_backend() != StorageBackend.MINIO or not artifact_refs:
        result["status"] = "not_expected"
    else:
        result["status"] = "pending"
    if result["status"] != "pending":
        for item in result["objects"]:
            item["status"] = result["status"]
    return result


def _log_replication_event(
    *,
    run_id: int,
    bucket: str | None,
    object_key: str | None,
    source_object_key: str | None,
    status: str,
    attempt: int,
    artifact_type: str | None = None,
    size_bytes: int | None = None,
    error_class: str | None = None,
) -> None:
    event: dict[str, Any] = {
        "event": "storage_audit.s3_replication",
        "run_id": run_id,
        "bucket": bucket,
        "object_key": object_key,
        "source_object_key": source_object_key,
        "status": status,
        "attempt": attempt,
        "artifact_type": artifact_type,
    }
    if size_bytes is not None:
        event["size_bytes"] = size_bytes
    if error_class:
        event["error_class"] = error_class
    try:
        logger.bind(storage_audit=True, **event).info(
            "storage_audit.s3_replication {}",
            json.dumps(event, sort_keys=True, separators=(",", ":")),
        )
    except Exception:  # noqa: BLE001 - audit logging must never affect a job
        logger.debug("Unable to emit S3 replication audit for run {}", run_id)


def _error_class(exc: Exception) -> str:
    """Prefer provider error codes such as AccessDenied over ClientError."""
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code") or type(exc).__name__)
    return type(exc).__name__


async def schedule_s3_replication(
    run_id: int, audit: dict[str, Any]
) -> dict[str, Any]:
    """Enqueue replication only after primary artifact writes succeeded."""
    refs = _artifact_refs(audit)
    result = secondary_status(refs)
    if result["status"] != "pending":
        return result

    try:
        from api.tasks.arq import enqueue_job

        await enqueue_job(
            FunctionNames.REPLICATE_WORKFLOW_RUN_ARTIFACTS_S3,
            run_id,
            refs,
            _job_id=f"s3-secondary-{run_id}",
        )
    except Exception as exc:  # noqa: BLE001 - primary completion is independent
        result["status"] = "failed"
        result["error_class"] = type(exc).__name__
        for item in result["objects"]:
            item["status"] = "failed"
        _log_replication_event(
            run_id=run_id,
            bucket=AWS_RECORDINGS_BUCKET,
            object_key=None,
            source_object_key=None,
            status="failed",
            attempt=0,
            error_class=type(exc).__name__,
        )
        logger.warning(
            "S3 secondary replication could not be scheduled for run {} ({})",
            run_id,
            type(exc).__name__,
        )
    return result


async def _replicate_one(
    s3_storage: S3FileSystem,
    run_id: int,
    ref: dict[str, Any],
) -> dict[str, Any]:
    source_key = ref["object_key"]
    destination_key = _destination_key(source_key)
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with tempfile.TemporaryDirectory(prefix="dograh-s3-secondary-") as temp_dir:
                source_path = str(Path(temp_dir) / "artifact")
                if not await storage_fs.adownload_file(source_key, source_path):
                    raise RuntimeError("PrimaryObjectUnavailable")
                size_bytes = ref.get("size_bytes")
                if size_bytes is None:
                    size_bytes = Path(source_path).stat().st_size
                if hasattr(s3_storage, "aupload_file_checked"):
                    await s3_storage.aupload_file_checked(source_path, destination_key)
                elif not await s3_storage.aupload_file(source_path, destination_key):
                    raise RuntimeError("S3UploadFailed")
            _log_replication_event(
                run_id=run_id,
                bucket=AWS_RECORDINGS_BUCKET,
                object_key=destination_key,
                source_object_key=source_key,
                status="success",
                attempt=attempt,
                artifact_type=ref.get("type"),
                size_bytes=size_bytes,
            )
            return {
                **ref,
                "object_key": destination_key,
                "size_bytes": size_bytes,
                "status": "success",
                "attempt": attempt,
            }
        except Exception as exc:  # noqa: BLE001 - bounded, isolated retry
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                logger.warning(
                    "S3 secondary replication attempt {} failed for run {} ({})",
                    attempt,
                    run_id,
                    type(exc).__name__,
                )
                await asyncio.sleep(_BACKOFF_SECONDS[attempt - 1])

    error_class = _error_class(last_error) if last_error else "UnknownError"
    _log_replication_event(
        run_id=run_id,
        bucket=AWS_RECORDINGS_BUCKET,
        object_key=destination_key,
        source_object_key=source_key,
        status="failed",
        attempt=MAX_ATTEMPTS,
        artifact_type=ref.get("type"),
        size_bytes=ref.get("size_bytes"),
        error_class=error_class,
    )
    return {
        **ref,
        "object_key": destination_key,
        "status": "failed",
        "attempt": MAX_ATTEMPTS,
        "error_class": error_class,
    }


async def replicate_workflow_run_artifacts_to_s3(
    _ctx: Any,
    workflow_run_id: int,
    artifact_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """ARQ entrypoint; never raises secondary-copy failures to callers."""
    config = secondary_status(artifact_refs)
    if config["status"] != "pending":
        return config

    try:
        s3_storage = S3FileSystem(
            bucket_name=AWS_RECORDINGS_BUCKET,
            region_name=AWS_REGION,
        )
    except Exception as exc:  # noqa: BLE001 - disabled/isolated secondary path
        error_class = _error_class(exc)
        logger.error(
            "Unable to initialize S3 secondary storage for run {} ({})",
            workflow_run_id,
            error_class,
        )
        for ref in artifact_refs:
            _log_replication_event(
                run_id=workflow_run_id,
                bucket=AWS_RECORDINGS_BUCKET,
                object_key=_destination_key(ref["object_key"]),
                source_object_key=ref["object_key"],
                status="failed",
                attempt=0,
                artifact_type=ref.get("type"),
                error_class=error_class,
            )
        return {**config, "status": "failed", "error_class": error_class}

    results = [
        await _replicate_one(s3_storage, workflow_run_id, ref)
        for ref in artifact_refs
    ]
    final_status = "success" if all(item["status"] == "success" for item in results) else "failed"
    return {**config, "status": final_status, "objects": results}
