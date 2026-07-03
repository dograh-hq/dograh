"""Upload end-of-call artifacts (recordings, transcript) to object storage.

Called from the pipeline process itself, straight from the in-memory call
buffers, so no local file ever has to cross a process/host boundary (no
shared /tmp between web and ARQ workers). Uploads happen before the
workflow-completion job is enqueued so QA and webhooks see the artifacts
in storage.
"""

from loguru import logger

from api.db import db_client
from api.services.storage import get_current_storage_backend, storage_fs


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
