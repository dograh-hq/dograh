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
