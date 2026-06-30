"""Provider-agnostic call status processing.

Extracted from ``api/routes/telephony.py`` so that per-provider route
modules can import the processor and normalized request type without
introducing a circular import on the routes module.
"""

from datetime import UTC, datetime
from typing import Optional

from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.enums import TelephonyCallStatus, WorkflowRunState
from api.services.campaign.campaign_call_dispatcher import campaign_call_dispatcher
from api.services.campaign.campaign_event_publisher import (
    get_campaign_event_publisher,
)
from api.services.campaign.circuit_breaker import circuit_breaker
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

TERMINAL_NOT_CONNECTED_STATUSES = frozenset(
    {
        TelephonyCallStatus.FAILED,
        TelephonyCallStatus.BUSY,
        TelephonyCallStatus.NO_ANSWER,
        TelephonyCallStatus.CANCELED,
        TelephonyCallStatus.ERROR,
    }
)
IN_FLIGHT_STATUSES = frozenset(
    {
        TelephonyCallStatus.INITIATED,
        TelephonyCallStatus.RINGING,
        TelephonyCallStatus.IN_PROGRESS,
        TelephonyCallStatus.ANSWERED,
    }
)
RETRYABLE_NOT_CONNECTED_STATUSES = frozenset(
    {TelephonyCallStatus.BUSY, TelephonyCallStatus.NO_ANSWER}
)
FAILURE_NOT_CONNECTED_STATUSES = frozenset(
    {TelephonyCallStatus.ERROR, TelephonyCallStatus.FAILED}
)


def _status_value(value: object) -> str:
    status = TelephonyCallStatus.from_raw(value)
    if status is not None:
        return status.value

    return str(value or "").lower()


def _duration_seconds(duration: str | None) -> int | float:
    if duration in (None, ""):
        return 0

    try:
        parsed = float(duration)
    except (TypeError, ValueError):
        return 0

    return int(parsed) if parsed.is_integer() else parsed


def _append_unique_tags(existing_tags: object, new_tags: list[str]) -> list[str]:
    tags = existing_tags if isinstance(existing_tags, list) else []
    merged = list(tags)
    for tag in new_tags:
        if tag not in merged:
            merged.append(tag)
    return merged


async def _enqueue_integrations_for_unconnected_run(
    workflow_run_id: int,
    status: str,
) -> None:
    """Fire post-call integrations (e.g. webhooks) when a call ends before the
    Pipecat pipeline ever starts.

    Enqueues integrations only -- deliberately *not*
    ``PROCESS_WORKFLOW_COMPLETION`` -- so an unconnected call still triggers the
    configured webhooks without incurring platform-usage billing.
    """
    await enqueue_job(FunctionNames.RUN_INTEGRATIONS_POST_WORKFLOW_RUN, workflow_run_id)
    logger.info(
        f"[run {workflow_run_id}] Enqueued post-call integrations after terminal "
        f"telephony status: {status}"
    )


class StatusCallbackRequest(BaseModel):
    """Normalized status callback shape used across all telephony providers.

    Provider-specific route handlers map raw webhook payloads into this shape,
    then hand it off to :func:`_process_status_update`.
    """

    call_id: str
    status: TelephonyCallStatus | str
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    direction: Optional[str] = None
    duration: Optional[str] = None

    extra: dict = {}


async def _process_status_update(workflow_run_id: int, status: StatusCallbackRequest):
    """Process status updates from telephony providers.

    Idempotent: handles repeated callbacks (e.g. from both webhook and CDR).
    """
    normalized_status = TelephonyCallStatus.from_raw(status.status)
    status_value = _status_value(status.status)
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found in status update"
        )
        return

    telephony_callback_logs = workflow_run.logs.get("telephony_status_callbacks", [])
    telephony_callback_log = {
        "status": status_value,
        "timestamp": datetime.now(UTC).isoformat(),
        "call_id": status.call_id,
        "duration": status.duration,
        **status.extra,
    }
    telephony_callback_logs.append(telephony_callback_log)

    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        logs={"telephony_status_callbacks": telephony_callback_logs},
    )

    if normalized_status == TelephonyCallStatus.COMPLETED:
        logger.info(
            f"[run {workflow_run_id}] Call completed with duration: {status.duration}s"
        )

        if workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)
            await circuit_breaker.record_and_evaluate(
                workflow_run.campaign_id, is_failure=False
            )

        if workflow_run.state != WorkflowRunState.COMPLETED.value:
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                is_completed=True,
                state=WorkflowRunState.COMPLETED.value,
            )

    elif normalized_status in TERMINAL_NOT_CONNECTED_STATUSES:
        logger.warning(
            f"[run {workflow_run_id}] Call failed with status: {normalized_status.value}"
        )

        if workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)
            is_failure = normalized_status in FAILURE_NOT_CONNECTED_STATUSES
            await circuit_breaker.record_and_evaluate(
                workflow_run.campaign_id,
                is_failure=is_failure,
                workflow_run_id=workflow_run_id if is_failure else None,
                reason=normalized_status.value if is_failure else None,
            )

        if (
            normalized_status in RETRYABLE_NOT_CONNECTED_STATUSES
            and workflow_run.campaign_id
        ):
            publisher = await get_campaign_event_publisher()
            await publisher.publish_retry_needed(
                workflow_run_id=workflow_run_id,
                reason=normalized_status.value.replace("-", "_"),
                campaign_id=workflow_run.campaign_id,
                queued_run_id=workflow_run.queued_run_id,
            )

        call_tags = (
            workflow_run.gathered_context.get("call_tags", [])
            if workflow_run.gathered_context
            else []
        )
        call_tags = _append_unique_tags(
            call_tags,
            ["not_connected", f"telephony_{normalized_status.value}"],
        )

        gathered_context = {
            "call_tags": call_tags,
            "call_disposition": normalized_status.value,
            "mapped_call_disposition": normalized_status.value,
        }
        if status.call_id:
            gathered_context["call_id"] = status.call_id

        should_run_post_call_integrations = (
            workflow_run.state == WorkflowRunState.INITIALIZED.value
            and not workflow_run.is_completed
        )

        update_kwargs = {
            "run_id": workflow_run_id,
            "is_completed": True,
            "state": WorkflowRunState.COMPLETED.value,
            "gathered_context": gathered_context,
        }
        if should_run_post_call_integrations:
            update_kwargs["usage_info"] = {
                "call_duration_seconds": _duration_seconds(status.duration)
            }

        await db_client.update_workflow_run(**update_kwargs)

        if should_run_post_call_integrations:
            await _enqueue_integrations_for_unconnected_run(
                workflow_run_id, normalized_status.value
            )
    elif normalized_status in IN_FLIGHT_STATUSES:
        # No-op while the call is in flight.
        pass
    else:
        logger.warning(
            f"[run {workflow_run_id}] Unexpected status update: {status.status}"
        )
