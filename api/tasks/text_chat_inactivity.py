"""Periodic completion of text chats abandoned without an explicit end."""

from datetime import UTC, datetime, timedelta

from loguru import logger
from pipecat.utils.enums import EndTaskReason
from pipecat.utils.run_context import set_current_run_id

from api.constants import (
    MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
    TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS,
    TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
)
from api.db import db_client
from api.db.models import WorkflowRunTextSessionModel
from api.services.workflow.text_chat_session_service import (
    TextChatSessionRevisionConflictError,
    complete_text_chat_session,
)


async def sweep_inactive_text_chat_sessions(_ctx) -> None:
    """End incomplete text chats that have exceeded the inactivity timeout."""
    completed_at = datetime.now(UTC)
    candidate_inactive_before = completed_at - timedelta(
        seconds=MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS
    )
    candidate_active_since = completed_at - timedelta(
        seconds=TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS
    )
    page_size = 100
    after_run_id = 0
    completed_count = 0

    while True:
        inactive_sessions = await db_client.get_inactive_workflow_run_text_sessions(
            active_since=candidate_active_since,
            inactive_before=candidate_inactive_before,
            limit=page_size,
            after_run_id=after_run_id,
        )
        if not inactive_sessions:
            break

        for text_session in inactive_sessions:
            inactivity_deadline = _text_chat_inactivity_deadline(
                text_session,
                inactivity_timeout_seconds=_text_chat_inactivity_timeout_seconds(
                    text_session
                ),
            )
            if inactivity_deadline > completed_at:
                continue

            run_id = text_session.workflow_run_id
            set_current_run_id(run_id)
            try:
                await complete_text_chat_session(
                    run_id=run_id,
                    text_session=text_session,
                    expected_revision=text_session.revision,
                    completion_reason=(
                        EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value
                    ),
                    completed_at=inactivity_deadline,
                )
                completed_count += 1
            except TextChatSessionRevisionConflictError:
                logger.debug(
                    f"Text chat {run_id} became active during inactivity sweep"
                )
            except Exception as e:  # noqa: BLE001 - one run must not stop the sweep
                logger.error(f"Failed to complete inactive text chat {run_id}: {e}")

        after_run_id = inactive_sessions[-1].workflow_run_id
        if len(inactive_sessions) < page_size:
            break

    if completed_count:
        logger.info(f"Completed {completed_count} inactive text chat session(s)")


def _text_chat_inactivity_timeout_seconds(
    text_session: WorkflowRunTextSessionModel,
) -> int:
    """Resolve the timeout captured by the workflow definition for this run."""
    workflow_run = text_session.workflow_run
    definition = getattr(workflow_run, "definition", None)
    if definition is not None:
        configurations = getattr(definition, "workflow_configurations", None)
    else:
        workflow = getattr(workflow_run, "workflow", None)
        configurations = getattr(workflow, "workflow_configurations", None)

    configured_timeout = (configurations or {}).get(
        "text_chat_inactivity_timeout_seconds",
        TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
    )
    try:
        timeout_seconds = int(configured_timeout)
    except (TypeError, ValueError):
        timeout_seconds = TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS
    return max(MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS, timeout_seconds)


def _text_chat_inactivity_deadline(
    text_session: WorkflowRunTextSessionModel,
    *,
    inactivity_timeout_seconds: int,
) -> datetime:
    """Return the timeout instant without adding cron scheduling delay."""
    last_activity_at = (
        text_session.updated_at
        or text_session.created_at
        or text_session.workflow_run.created_at
    )
    if last_activity_at is None:
        return datetime.now(UTC)
    if last_activity_at.tzinfo is None:
        last_activity_at = last_activity_at.replace(tzinfo=UTC)
    return last_activity_at + timedelta(seconds=inactivity_timeout_seconds)
