import asyncio
import os

from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.services.workflow_run_artifacts import upload_workflow_run_artifacts
from api.services.workflow_run_billing import (
    report_completed_workflow_run_platform_usage,
)
from api.tasks.run_integrations import run_integrations_post_workflow_run


def _read_and_remove_temp_file(temp_file_path: str | None, label: str) -> bytes | None:
    if not temp_file_path:
        return None
    try:
        if not os.path.exists(temp_file_path):
            logger.warning(f"{label} temp file not found: {temp_file_path}")
            return None
        with open(temp_file_path, "rb") as f:
            data = f.read()
        os.remove(temp_file_path)
        return data
    except Exception as e:
        logger.error(f"Error reading legacy {label} temp file {temp_file_path}: {e}")
        return None


async def _upload_legacy_temp_artifacts(
    workflow_run_id: int,
    audio_temp_path: str | None,
    transcript_temp_path: str | None,
    user_audio_temp_path: str | None,
    bot_audio_temp_path: str | None,
) -> None:
    """Handle jobs enqueued before uploads moved into the pipeline process.

    Pre-refactor web workers passed local temp-file paths; upload them if this
    worker can still see the files (same host / shared volume).

    Deprecated: remove once no pre-refactor jobs can remain in the queue.
    """
    logger.info(
        f"Processing legacy temp-file artifacts for workflow run {workflow_run_id}"
    )
    transcript_bytes = await asyncio.to_thread(
        _read_and_remove_temp_file, transcript_temp_path, "transcript"
    )
    await upload_workflow_run_artifacts(
        workflow_run_id,
        mixed_audio_wav=await asyncio.to_thread(
            _read_and_remove_temp_file, audio_temp_path, "mixed audio"
        ),
        user_audio_wav=await asyncio.to_thread(
            _read_and_remove_temp_file, user_audio_temp_path, "user audio"
        ),
        bot_audio_wav=await asyncio.to_thread(
            _read_and_remove_temp_file, bot_audio_temp_path, "bot audio"
        ),
        transcript_text=(
            transcript_bytes.decode("utf-8") if transcript_bytes else None
        ),
    )


async def process_workflow_completion(
    _ctx,
    workflow_run_id: int,
    audio_temp_path: str | None = None,
    transcript_temp_path: str | None = None,
    user_audio_temp_path: str | None = None,
    bot_audio_temp_path: str | None = None,
):
    """Process workflow completion: run integrations and report billing.

    Recording/transcript uploads happen in the pipeline process itself
    (api/services/workflow_run_artifacts.py) before this job is enqueued,
    so this task needs no shared filesystem with the web tier. The temp-path
    arguments only exist for jobs enqueued by pre-refactor web workers.

    Args:
        _ctx: ARQ context (unused)
        workflow_run_id: The workflow run ID
        audio_temp_path: Deprecated, pre-refactor jobs only
        transcript_temp_path: Deprecated, pre-refactor jobs only
        user_audio_temp_path: Deprecated, pre-refactor jobs only
        bot_audio_temp_path: Deprecated, pre-refactor jobs only
    """
    run_id = str(workflow_run_id)
    set_current_run_id(run_id)

    logger.info(f"Processing workflow completion for run {workflow_run_id}")

    if (
        audio_temp_path
        or transcript_temp_path
        or user_audio_temp_path
        or bot_audio_temp_path
    ):
        await _upload_legacy_temp_artifacts(
            workflow_run_id,
            audio_temp_path,
            transcript_temp_path,
            user_audio_temp_path,
            bot_audio_temp_path,
        )

    # Run integrations including QA analysis (after uploads are complete)
    try:
        await run_integrations_post_workflow_run(_ctx, workflow_run_id)
    except Exception as e:
        logger.error(f"Error running integrations for workflow {workflow_run_id}: {e}")

    # Notify MPS after completion. MPS owns credit accounting.
    try:
        await report_completed_workflow_run_platform_usage(workflow_run_id)
    except Exception as e:
        logger.error(
            f"Error reporting platform usage for workflow {workflow_run_id}: {e}"
        )

    logger.info(f"Completed workflow completion processing for run {workflow_run_id}")
