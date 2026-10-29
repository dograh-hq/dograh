"""Durable handoff of a completed text chat's post-commit work."""

from arq import Retry
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.services.workflow.text_chat_session_service import (
    finalize_completed_text_chat as finalize_session,
)


async def finalize_completed_text_chat(_ctx, run_id: int) -> None:
    set_current_run_id(run_id)
    try:
        await finalize_session(run_id)
    except Exception:
        logger.exception("Failed to finalize completed text chat {}", run_id)
        raise Retry(defer=5)
