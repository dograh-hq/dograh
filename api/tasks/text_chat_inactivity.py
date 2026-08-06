"""Periodic completion of text chats abandoned without an explicit end."""

from datetime import UTC, datetime, timedelta

from loguru import logger
from pipecat.utils.enums import EndTaskReason
from pipecat.utils.run_context import set_current_run_id

from api.constants import (
    MAX_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
    MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
    TEXT_CHAT_INACTIVITY_SWEEP_ENQUEUE_LIMIT,
    TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS,
    TEXT_CHAT_INACTIVITY_SWEEP_MAX_PAGES,
    TEXT_CHAT_INACTIVITY_SWEEP_PAGE_SIZE,
    TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
)
from api.db import db_client
from api.db.models import WorkflowRunTextSessionModel
from api.enums import WorkflowRunMode
from api.services.workflow.text_chat_session_service import (
    TextChatSessionRevisionConflictError,
    complete_text_chat_session,
)
from api.tasks.function_names import FunctionNames


async def sweep_inactive_text_chat_sessions(_ctx) -> None:
    """Enqueue bounded, retryable completion work for inactive text chats."""
    completed_at = datetime.now(UTC)
    candidate_inactive_before = completed_at - timedelta(
        seconds=MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS
    )
    candidate_active_since = completed_at - timedelta(
        seconds=TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS
    )
    after_run_id = 0
    enqueued_count = 0

    for _ in range(TEXT_CHAT_INACTIVITY_SWEEP_MAX_PAGES):
        inactive_sessions = await db_client.get_inactive_workflow_run_text_sessions(
            active_since=candidate_active_since,
            inactive_before=candidate_inactive_before,
            limit=TEXT_CHAT_INACTIVITY_SWEEP_PAGE_SIZE,
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
            try:
                await _enqueue_inactive_text_chat_completion(
                    run_id=run_id,
                    expected_revision=text_session.revision,
                    completed_at=inactivity_deadline,
                )
                enqueued_count += 1
            except Exception as e:  # noqa: BLE001 - retry on the next sweep
                logger.error(f"Failed to enqueue inactive text chat {run_id}: {e}")

            if enqueued_count >= TEXT_CHAT_INACTIVITY_SWEEP_ENQUEUE_LIMIT:
                break

        after_run_id = inactive_sessions[-1].workflow_run_id
        if enqueued_count >= TEXT_CHAT_INACTIVITY_SWEEP_ENQUEUE_LIMIT:
            break
        if len(inactive_sessions) < TEXT_CHAT_INACTIVITY_SWEEP_PAGE_SIZE:
            break
    else:
        logger.warning(
            "Inactive text chat sweep reached its candidate scan limit; "
            "remaining sessions will be considered on the next run"
        )

    if enqueued_count:
        logger.info(f"Enqueued {enqueued_count} inactive text chat completion(s)")


async def _enqueue_inactive_text_chat_completion(
    *,
    run_id: int,
    expected_revision: int,
    completed_at: datetime,
) -> None:
    """Fan completion out of the cron so storage and integrations cannot stall it."""
    from api.tasks.arq import enqueue_job  # lazy import avoids circular import

    await enqueue_job(
        FunctionNames.COMPLETE_INACTIVE_TEXT_CHAT_SESSION,
        run_id,
        expected_revision,
        completed_at.isoformat(),
        _job_id=f"inactive-text-chat-completion-{run_id}-{expected_revision}",
    )


async def complete_inactive_text_chat_session(
    _ctx,
    run_id: int,
    expected_revision: int,
    completed_at_iso: str,
) -> None:
    """Complete one inactivity candidate after reloading it through its owner org."""
    set_current_run_id(run_id)
    organization_id = await db_client.get_organization_id_by_workflow_run_id(run_id)
    if organization_id is None:
        logger.warning(f"Skipping inactive text chat {run_id}: organization not found")
        return

    text_session = await db_client.get_workflow_run_text_session(
        run_id,
        organization_id=organization_id,
    )
    if not text_session or not text_session.workflow_run:
        logger.warning(f"Skipping inactive text chat {run_id}: session not found")
        return
    workflow_run = text_session.workflow_run
    if workflow_run.mode != WorkflowRunMode.TEXTCHAT.value:
        return

    completed_at = datetime.fromisoformat(completed_at_iso)
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)
    try:
        await complete_text_chat_session(
            run_id=run_id,
            text_session=text_session,
            expected_revision=expected_revision,
            completion_reason=EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value,
            completed_at=completed_at,
        )
    except TextChatSessionRevisionConflictError:
        logger.debug(f"Text chat {run_id} became active before inactivity completion")


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
    return min(
        MAX_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
        max(MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS, timeout_seconds),
    )


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
