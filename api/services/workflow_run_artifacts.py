"""Store and retrieve end-of-call artifacts (recordings, transcript).

Uploads are called from the pipeline process itself, straight from the
in-memory call buffers, so no local file ever has to cross a process/host
boundary (no shared /tmp between web and ARQ workers). Uploads happen
before the workflow-completion job is enqueued so QA and webhooks see the
artifacts in storage. The read-back helper (`download_run_transcript_text`)
is the counterpart used by consumers that only have the persisted run.
"""

import asyncio
import os
import tempfile

from loguru import logger

from api.db import db_client
from api.services.storage import (
    get_current_storage_backend,
    get_storage_for_backend,
    storage_fs,
)


class TranscriptDownloadError(Exception):
    """A transcript exists for the run but could not be fetched from storage."""


def _recording_metadata(storage_key: str, storage_backend: str, track: str) -> dict:
    return {
        "storage_key": storage_key,
        "storage_backend": storage_backend,
        "format": "wav",
        "track": track,
    }


async def _upload_bytes(
    workflow_run_id: int,
    data: bytes,
    storage_key: str,
    label: str,
) -> bool:
    try:
        logger.debug(f"{label} size: {len(data)} bytes")
        if await storage_fs.acreate_file_from_bytes(storage_key, data):
            logger.info(f"Successfully uploaded {label}: {storage_key}")
            return True
        logger.error(
            f"Storage backend rejected {label} upload for workflow "
            f"{workflow_run_id}: {storage_key}"
        )
        return False
    except Exception as e:
        logger.error(f"Error uploading {label} for workflow {workflow_run_id}: {e}")
        return False


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

    recordings_metadata: dict[str, dict] = {}

    if mixed_audio_wav:
        recording_url = f"recordings/{workflow_run_id}.wav"
        logger.info(
            f"Uploading mixed audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, mixed_audio_wav, recording_url, "mixed audio"
        ):
            recordings_metadata["mixed"] = _recording_metadata(
                recording_url, storage_backend.value, "mixed"
            )
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                recording_url=recording_url,
                storage_backend=storage_backend.value,
            )

    if user_audio_wav:
        user_recording_url = f"recordings/{workflow_run_id}/user.wav"
        logger.info(
            f"Uploading user audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, user_audio_wav, user_recording_url, "user audio"
        ):
            recordings_metadata["user"] = _recording_metadata(
                user_recording_url, storage_backend.value, "user"
            )

    if bot_audio_wav:
        bot_recording_url = f"recordings/{workflow_run_id}/bot.wav"
        logger.info(
            f"Uploading bot audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id, bot_audio_wav, bot_recording_url, "bot audio"
        ):
            recordings_metadata["bot"] = _recording_metadata(
                bot_recording_url, storage_backend.value, "bot"
            )

    if recordings_metadata:
        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            storage_backend=storage_backend.value,
            extra={"recordings": recordings_metadata},
        )

    if transcript_text:
        transcript_url = f"transcripts/{workflow_run_id}.txt"
        logger.info(
            f"Uploading transcript to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_bytes(
            workflow_run_id,
            transcript_text.encode("utf-8"),
            transcript_url,
            "transcript",
        ):
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                transcript_url=transcript_url,
                storage_backend=storage_backend.value,
            )


def _read_transcript_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


async def download_run_transcript_text(
    *, transcript_url: str | None, storage_backend: str
) -> str:
    """Return the stored transcript text for a run.

    Returns "" when no transcript was recorded (``transcript_url`` is empty).
    Raises ``ValueError`` for an unknown storage backend and
    ``TranscriptDownloadError`` when the object exists but the download
    fails — so callers can tell "no transcript" apart from "couldn't fetch
    it" instead of conflating both as empty.
    """
    if not transcript_url:
        return ""

    # Raises ValueError for an unrecognized backend; let it propagate.
    storage = get_storage_for_backend(storage_backend)

    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)

        ok = await storage.adownload_file(transcript_url, tmp_path)
        if not ok:
            raise TranscriptDownloadError(
                f"transcript object {transcript_url!r} could not be "
                f"downloaded from {storage_backend} storage"
            )

        return await asyncio.to_thread(_read_transcript_file, tmp_path)
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
