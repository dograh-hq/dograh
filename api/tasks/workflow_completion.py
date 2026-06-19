import os
from typing import Optional

from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.services.storage import get_current_storage_backend, storage_fs
from api.services.workflow_run_billing import (
    report_completed_workflow_run_platform_usage,
)
from api.tasks.run_integrations import run_integrations_post_workflow_run


def _recording_metadata(storage_key: str, storage_backend: str, track: str) -> dict:
    return {
        "storage_key": storage_key,
        "storage_backend": storage_backend,
        "format": "wav",
        "track": track,
    }


async def _upload_temp_file(
    workflow_run_id: int,
    temp_file_path: str,
    storage_key: str,
    label: str,
) -> bool:
    try:
        if not os.path.exists(temp_file_path):
            logger.warning(f"{label} temp file not found: {temp_file_path}")
            return False

        file_size = os.path.getsize(temp_file_path)
        logger.debug(f"{label} file size: {file_size} bytes")

        await storage_fs.aupload_file(temp_file_path, storage_key)
        logger.info(f"Successfully uploaded {label}: {storage_key}")
        return True
    except Exception as e:
        logger.error(f"Error uploading {label} for workflow {workflow_run_id}: {e}")
        return False
    finally:
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"Cleaned up temp {label} file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp {label} file: {e}")


async def process_workflow_completion(
    _ctx,
    workflow_run_id: int,
    audio_temp_path: Optional[str] = None,
    transcript_temp_path: Optional[str] = None,
    user_audio_temp_path: Optional[str] = None,
    bot_audio_temp_path: Optional[str] = None,
):
    """Process workflow completion: upload artifacts and run integrations.

    This task combines audio upload, transcript upload, and webhook integrations
    into a single sequential task to ensure integrations run after uploads complete.

    Args:
        _ctx: ARQ context (unused)
        workflow_run_id: The workflow run ID
        audio_temp_path: Optional path to temp audio file
        transcript_temp_path: Optional path to temp transcript file
        user_audio_temp_path: Optional path to temp user-track audio file
        bot_audio_temp_path: Optional path to temp bot-track audio file
    """
    run_id = str(workflow_run_id)
    set_current_run_id(run_id)

    logger.info(f"Processing workflow completion for run {workflow_run_id}")

    storage_backend = get_current_storage_backend()

    # Step 1: Upload audio if provided
    recordings_metadata: dict[str, dict] = {}

    if audio_temp_path:
        recording_url = f"recordings/{workflow_run_id}.wav"
        logger.info(
            f"Uploading mixed audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_temp_file(
            workflow_run_id, audio_temp_path, recording_url, "mixed audio"
        ):
            recordings_metadata["mixed"] = _recording_metadata(
                recording_url, storage_backend.value, "mixed"
            )
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                recording_url=recording_url,
                storage_backend=storage_backend.value,
            )

    if user_audio_temp_path:
        user_recording_url = f"recordings/{workflow_run_id}/user.wav"
        logger.info(
            f"Uploading user audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_temp_file(
            workflow_run_id, user_audio_temp_path, user_recording_url, "user audio"
        ):
            recordings_metadata["user"] = _recording_metadata(
                user_recording_url, storage_backend.value, "user"
            )

    if bot_audio_temp_path:
        bot_recording_url = f"recordings/{workflow_run_id}/bot.wav"
        logger.info(
            f"Uploading bot audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
        )
        if await _upload_temp_file(
            workflow_run_id, bot_audio_temp_path, bot_recording_url, "bot audio"
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

    # Step 2: Upload transcript if provided
    if transcript_temp_path:
        try:
            if os.path.exists(transcript_temp_path):
                file_size = os.path.getsize(transcript_temp_path)
                logger.debug(f"Transcript file size: {file_size} bytes")

                transcript_url = f"transcripts/{workflow_run_id}.txt"
                logger.info(
                    f"Uploading transcript to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
                )

                await storage_fs.aupload_file(transcript_temp_path, transcript_url)
                await db_client.update_workflow_run(
                    run_id=workflow_run_id,
                    transcript_url=transcript_url,
                    storage_backend=storage_backend.value,
                )
                logger.info(f"Successfully uploaded transcript: {transcript_url}")
            else:
                logger.warning(
                    f"Transcript temp file not found: {transcript_temp_path}"
                )
        except Exception as e:
            logger.error(
                f"Error uploading transcript for workflow {workflow_run_id}: {e}"
            )
        finally:
            if transcript_temp_path and os.path.exists(transcript_temp_path):
                try:
                    os.remove(transcript_temp_path)
                    logger.debug(
                        f"Cleaned up temp transcript file: {transcript_temp_path}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to clean up temp transcript file: {e}")

    # Step 3: Run integrations including QA analysis (after uploads are complete)
    try:
        await run_integrations_post_workflow_run(_ctx, workflow_run_id)
    except Exception as e:
        logger.error(f"Error running integrations for workflow {workflow_run_id}: {e}")

    # Step 4: Notify MPS after completion. MPS owns credit accounting.
    try:
        await report_completed_workflow_run_platform_usage(workflow_run_id)
    except Exception as e:
        logger.error(
            f"Error reporting platform usage for workflow {workflow_run_id}: {e}"
        )

    logger.info(f"Completed workflow completion processing for run {workflow_run_id}")
