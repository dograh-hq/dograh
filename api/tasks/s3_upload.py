import os

from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.services.storage import storage_fs


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
