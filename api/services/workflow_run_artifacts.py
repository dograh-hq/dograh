"""Upload end-of-call artifacts (recordings, transcript) to object storage.

Called from the pipeline process itself, straight from the in-memory call
buffers, so no local file ever has to cross a process/host boundary (no
shared /tmp between web and ARQ workers). Uploads happen before the
workflow-completion job is enqueued so QA and webhooks see the artifacts
in storage.
"""

import asyncio
import hashlib
import io
import wave
from datetime import UTC, datetime

from loguru import logger

from api.constants import RECORD_CALLS
from api.db import db_client
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
        except Exception:
            logger.warning(
                "Storage upload attempt {} failed for workflow run {} ({})",
                attempt + 1,
                workflow_run_id,
                label,
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
) -> None:
    """Persist one artifact's metadata without stopping other artifact work."""
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
        except Exception:
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
    except Exception:
        logger.warning(
            "Recording metadata write failed for workflow run {} ({})",
            workflow_run_id,
            metadata["track"],
        )


async def _persist_run_fields(workflow_run_id: int, **fields) -> None:
    try:
        await db_client.update_workflow_run(run_id=workflow_run_id, **fields)
    except Exception:
        logger.warning(
            "Artifact metadata write failed for workflow run {}", workflow_run_id
        )


async def upload_workflow_run_artifacts(
    workflow_run_id: int,
    *,
    mixed_audio_wav: bytes | None = None,
    user_audio_wav: bytes | None = None,
    bot_audio_wav: bytes | None = None,
    transcript_text: str | None = None,
) -> None:
    """Upload call artifacts to object storage and persist their metadata.

    Each artifact is uploaded independently; a failure is logged and the
    remaining artifacts are still attempted.
    """
    storage_backend = get_current_storage_backend()
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
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

    if mixed_audio_wav:
        recording_url = f"recordings/{artifact_root}/call.wav"
        logger.info(
            f"Uploading mixed audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, mixed_audio_wav, recording_url, "mixed audio"
        ):
            recordings_metadata["mixed"] = _recording_metadata(
                recording_url, storage_backend.value, "mixed", mixed_audio_wav
            )
            await _persist_recording_metadata(
                workflow_run_id, recordings_metadata["mixed"], update_run=True
            )

    if user_audio_wav:
        user_recording_url = f"recordings/{artifact_root}/user.wav"
        logger.info(
            f"Uploading user audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, user_audio_wav, user_recording_url, "user audio"
        ):
            recordings_metadata["user"] = _recording_metadata(
                user_recording_url, storage_backend.value, "user", user_audio_wav
            )
            await _persist_recording_metadata(
                workflow_run_id, recordings_metadata["user"]
            )

    if bot_audio_wav:
        # Keep the legacy database/UI track label ("bot") while using the
        # product-facing S3 object name requested for new CALMOS calls.
        bot_recording_url = f"recordings/{artifact_root}/assistant.wav"
        logger.info(
            f"Uploading bot audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, bot_audio_wav, bot_recording_url, "bot audio"
        ):
            recordings_metadata["bot"] = _recording_metadata(
                bot_recording_url, storage_backend.value, "bot", bot_audio_wav
            )
            await _persist_recording_metadata(
                workflow_run_id, recordings_metadata["bot"]
            )

    if recordings_metadata:
        await _persist_run_fields(
            workflow_run_id,
            storage_backend=storage_backend.value,
            extra={"recordings": recordings_metadata},
        )

    if transcript_text:
        transcript_url = f"transcripts/{artifact_root}/transcript.txt"
        logger.info(
            f"Uploading transcript to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id,
            transcript_text.encode("utf-8"),
            transcript_url,
            "transcript",
        ):
            await _persist_run_fields(
                workflow_run_id,
                transcript_url=transcript_url,
                transcript_object_key=transcript_url,
                storage_backend=storage_backend.value,
                full_transcript=transcript_text,
            )
