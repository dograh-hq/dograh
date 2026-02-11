import os
from typing import Optional

from loguru import logger

from api.db import db_client
from api.services.storage import get_current_storage_backend, storage_fs
from api.tasks.run_integrations import run_integrations_post_workflow_run
from pipecat.utils.run_context import set_current_run_id


async def upload_voicemail_audio_to_s3(
    _ctx,
    workflow_run_id: int,
    temp_file_path: str,
    s3_key: str,
):
    """Upload voicemail detection audio from temp file to S3.

    Handles voicemail-specific paths and doesn't update the workflow run's
    recording_url field.

    Args:
        _ctx: ARQ context (unused)
        workflow_run_id: The workflow run ID
        temp_file_path: Path to the temporary WAV file
        s3_key: The S3 key where the file should be uploaded
    """
    run_id = str(workflow_run_id)
    set_current_run_id(run_id)

    logger.info(f"Starting voicemail audio upload to S3 from {temp_file_path}")

    try:
        # Verify temp file exists
        if not os.path.exists(temp_file_path):
            logger.error(f"Temp voicemail audio file not found: {temp_file_path}")
            raise FileNotFoundError(
                f"Temp voicemail audio file not found: {temp_file_path}"
            )

        file_size = os.path.getsize(temp_file_path)
        logger.debug(f"Voicemail audio file size: {file_size} bytes")

        # Upload to S3
        upload_ok = await storage_fs.aupload_file(temp_file_path, s3_key)

        if upload_ok:
            logger.info(f"Successfully uploaded voicemail audio to S3: {s3_key}")
        else:
            logger.error(
                f"Failed to upload voicemail audio to S3 for workflow {workflow_run_id}"
            )
            raise Exception(f"S3 upload failed for {s3_key}")

    except Exception as e:
        logger.error(
            f"Error uploading voicemail audio to S3 for workflow {workflow_run_id}: {e}"
        )
        raise
    finally:
        # Clean up temp file
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"Cleaned up temp voicemail audio file: {temp_file_path}")
            except Exception as e:
                logger.warning(
                    f"Failed to clean up temp voicemail audio file {temp_file_path}: {e}"
                )


async def process_workflow_completion(
    _ctx,
    workflow_run_id: int,
    audio_temp_path: Optional[str] = None,
    transcript_temp_path: Optional[str] = None,
):
    """Process workflow completion: upload artifacts and run integrations.

    This task combines audio upload, transcript upload, and webhook integrations
    into a single sequential task to ensure integrations run after uploads complete.

    Args:
        _ctx: ARQ context (unused)
        workflow_run_id: The workflow run ID
        audio_temp_path: Optional path to temp audio file
        transcript_temp_path: Optional path to temp transcript file
    """
    run_id = str(workflow_run_id)
    set_current_run_id(run_id)

    logger.info(f"Processing workflow completion for run {workflow_run_id}")

    storage_backend = get_current_storage_backend()

    # Step 1: Upload audio if provided
    if audio_temp_path:
        try:
            if os.path.exists(audio_temp_path):
                file_size = os.path.getsize(audio_temp_path)
                logger.debug(f"Audio file size: {file_size} bytes")

                recording_url = f"recordings/{workflow_run_id}.wav"
                logger.info(
                    f"Uploading audio to {storage_backend.name} - workflow_run_id: {workflow_run_id}"
                )

                await storage_fs.aupload_file(audio_temp_path, recording_url)
                await db_client.update_workflow_run(
                    run_id=workflow_run_id,
                    recording_url=recording_url,
                    storage_backend=storage_backend.value,
                )
                logger.info(f"Successfully uploaded audio: {recording_url}")
            else:
                logger.warning(f"Audio temp file not found: {audio_temp_path}")
        except Exception as e:
            logger.error(f"Error uploading audio for workflow {workflow_run_id}: {e}")
        finally:
            if audio_temp_path and os.path.exists(audio_temp_path):
                try:
                    os.remove(audio_temp_path)
                    logger.debug(f"Cleaned up temp audio file: {audio_temp_path}")
                except Exception as e:
                    logger.warning(f"Failed to clean up temp audio file: {e}")

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

    # Step 3: Run webhook integrations (after uploads are complete)
    try:
        await run_integrations_post_workflow_run(_ctx, workflow_run_id)
    except Exception as e:
        logger.error(f"Error running integrations for workflow {workflow_run_id}: {e}")

    logger.info(f"Completed workflow completion processing for run {workflow_run_id}")
