"""Durable, retrying delivery of outbound webhooks.

A workflow's final webhook must survive a transient network error. Rather than
firing the HTTP POST inline and forgetting it, ``run_integrations`` persists a
``WebhookDeliveryModel`` row and enqueues :func:`deliver_webhook`. This task sends
the request and, on a *transient* failure, schedules the next attempt with
exponential backoff -- up to ``max_attempts``, after which the delivery is parked
as ``dead_letter`` for inspection. Permanent failures (most 4xx) dead-letter
immediately instead of looping.

A periodic :func:`sweep_webhook_deliveries` cron re-enqueues any ``pending``
delivery whose attempt is overdue, so deliveries survive worker restarts / lost
ARQ jobs. The DB row is the source of truth; this task is idempotent and only
acts on a delivery that is still ``pending``.
"""

from datetime import UTC, datetime, timedelta
from typing import Optional

import httpx
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.constants import DEFAULT_WEBHOOK_DELIVERY_CONFIG
from api.db import db_client
from api.db.models import WebhookDeliveryModel
from api.tasks.function_names import FunctionNames
from api.utils.credential_auth import build_auth_header

# HTTP statuses that are worth retrying even though the server answered.
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _delivery_job_id(delivery_id: int, attempt_count: int) -> str:
    """Deterministic ARQ job id so duplicate enqueues (task re-enqueue + sweeper)
    collapse to one job instead of double-sending."""
    return f"webhook-delivery-{delivery_id}-{attempt_count}"


def _backoff_seconds(attempt: int) -> int:
    """Exponential backoff (capped) for the next attempt after `attempt` failures."""
    base = DEFAULT_WEBHOOK_DELIVERY_CONFIG["base_delay_seconds"]
    cap = DEFAULT_WEBHOOK_DELIVERY_CONFIG["max_delay_seconds"]
    return min(base * (2 ** (attempt - 1)), cap)


async def _enqueue_delivery(
    delivery_id: int,
    attempt_count: int,
    defer_by: int = 0,
    reclaim_token: Optional[int] = None,
):
    """Enqueue a delivery attempt with a dedup-safe job id.

    The normal (task self-retry) path uses a deterministic id so a retry and a
    sweeper pass for the *same* attempt collapse to one job. The sweeper passes a
    ``reclaim_token`` (the lease timestamp) to get a distinct id, so reconciling a
    delivered-but-unrecorded row is not deduped against the original attempt's
    already-completed job. The atomic claim still guarantees at most one send.
    """
    from api.tasks.arq import enqueue_job  # lazy import avoids circular import

    if reclaim_token is not None:
        job_id = f"webhook-delivery-reclaim-{delivery_id}-{reclaim_token}"
    else:
        job_id = _delivery_job_id(delivery_id, attempt_count)

    await enqueue_job(
        FunctionNames.DELIVER_WEBHOOK,
        delivery_id,
        _job_id=job_id,
        _defer_by=defer_by,
    )


async def _build_headers(delivery: WebhookDeliveryModel, attempt: int) -> dict:
    """Assemble request headers, re-resolving credential auth at send time so
    secrets are never persisted on the delivery row and rotation is honoured."""
    headers = {"Content-Type": "application/json"}

    if delivery.credential_uuid:
        credential = await db_client.get_credential_by_uuid(
            delivery.credential_uuid, delivery.organization_id
        )
        if credential:
            headers.update(build_auth_header(credential))
        else:
            logger.warning(
                f"Credential {delivery.credential_uuid} not found for webhook "
                f"'{delivery.webhook_name}' (delivery {delivery.id})"
            )

    for h in delivery.custom_headers or []:
        key, value = h.get("key"), h.get("value")
        if key and value:
            headers[key] = value

    # Stable idempotency signal so the receiver can dedupe retried deliveries.
    headers["X-Dograh-Delivery-Id"] = delivery.delivery_uuid
    headers["X-Dograh-Workflow-Run-Id"] = str(delivery.workflow_run_id)
    headers["X-Dograh-Delivery-Attempt"] = str(attempt)
    return headers


async def _handle_transient_failure(
    delivery: WebhookDeliveryModel,
    attempt: int,
    error: str,
    status_code: Optional[int],
) -> None:
    """Schedule a backed-off retry, or dead-letter once attempts are exhausted."""
    if attempt >= delivery.max_attempts:
        await db_client.mark_webhook_delivery_dead_letter(
            delivery.id, attempt, error, status_code
        )
        return

    delay = _backoff_seconds(attempt)
    scheduled_for = datetime.now(UTC) + timedelta(seconds=delay)
    await db_client.schedule_webhook_delivery_retry(
        delivery_id=delivery.id,
        attempt_count=attempt,
        scheduled_for=scheduled_for,
        last_error=error,
        last_status_code=status_code,
    )
    await _enqueue_delivery(delivery.id, attempt_count=attempt, defer_by=delay)
    logger.warning(
        f"Webhook '{delivery.webhook_name}' delivery {delivery.id} attempt {attempt} "
        f"failed ({error}); retrying in {delay}s "
        f"(attempt {attempt + 1}/{delivery.max_attempts})"
    )


async def deliver_webhook(_ctx, delivery_id: int) -> None:
    """Send one webhook delivery attempt and record the outcome.

    Concurrency-safe: the delivery is atomically *claimed* before the HTTP
    request (a conditional update only one worker can win), so a duplicate
    enqueue or sweeper re-injection cannot double-send. A claim that returns
    nothing means another worker owns it, or it is no longer pending/due -- a
    no-op.
    """
    # Lease long enough to outlast a full attempt so the sweeper does not reclaim
    # a delivery that is still in flight.
    lease_seconds = DEFAULT_WEBHOOK_DELIVERY_CONFIG["timeout_seconds"] + 60
    delivery = await db_client.claim_webhook_delivery(delivery_id, lease_seconds)
    if delivery is None:
        logger.debug(
            f"Webhook delivery {delivery_id} not claimable "
            f"(already claimed, not pending, or not yet due); skipping"
        )
        return

    set_current_run_id(str(delivery.workflow_run_id))
    attempt = delivery.attempt_count + 1
    method = (delivery.http_method or "POST").upper()
    timeout = DEFAULT_WEBHOOK_DELIVERY_CONFIG["timeout_seconds"]

    try:
        headers = await _build_headers(delivery, attempt)

        async with httpx.AsyncClient() as client:
            if method in ("POST", "PUT", "PATCH"):
                response = await client.request(
                    method=method,
                    url=delivery.endpoint_url,
                    json=delivery.payload,
                    headers=headers,
                    timeout=timeout,
                )
            else:  # GET, DELETE
                response = await client.request(
                    method=method,
                    url=delivery.endpoint_url,
                    headers=headers,
                    timeout=timeout,
                )

        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        error = f"HTTP {status_code}: {e.response.text[:200]}"
        if status_code in _RETRYABLE_STATUS_CODES:
            await _handle_transient_failure(delivery, attempt, error, status_code)
        else:
            # Permanent (auth/validation/not-found): retrying won't help. Park it.
            await db_client.mark_webhook_delivery_dead_letter(
                delivery.id, attempt, error, status_code
            )
        return
    except httpx.RequestError as e:
        # Connect/read timeouts, DNS, connection resets -- the transient class that
        # previously lost the webhook entirely. str(e) is often empty, so use repr.
        await _handle_transient_failure(delivery, attempt, repr(e), None)
        return
    except Exception as e:
        # Unexpected (e.g. a bug): don't loop on it, surface as dead-letter.
        logger.error(
            f"Webhook '{delivery.webhook_name}' delivery {delivery.id} "
            f"unexpected error: {e!r}"
        )
        await db_client.mark_webhook_delivery_dead_letter(
            delivery.id, attempt, repr(e), None
        )
        return

    # The receiver accepted the payload (2xx). Recording success must NOT be able
    # to dead-letter an already-delivered webhook: if this DB write fails, log and
    # leave the row claimed-but-pending so the sweeper reconciles it once the
    # lease expires (the receiver dedups the re-send via X-Dograh-Delivery-Id).
    try:
        await db_client.mark_webhook_delivery_succeeded(
            delivery.id, attempt, response.status_code
        )
        logger.info(
            f"Webhook '{delivery.webhook_name}' delivery {delivery.id} succeeded: "
            f"{response.status_code} (attempt {attempt})"
        )
    except Exception as e:
        logger.error(
            f"Webhook '{delivery.webhook_name}' delivery {delivery.id} was "
            f"delivered ({response.status_code}) but recording success failed; "
            f"leaving it for the sweeper to reconcile after the lease expires: {e!r}"
        )


async def sweep_webhook_deliveries(_ctx) -> None:
    """Safety net: re-enqueue pending deliveries whose attempt is overdue.

    Handles ARQ jobs lost to a worker restart or Redis flush. Re-enqueuing uses the
    same deterministic job id, so if the original deferred job still exists this is a
    no-op; it only re-injects genuinely lost work. ``deliver_webhook`` is idempotent.
    """
    page_size = 100
    after_id = 0
    total = 0
    while True:
        # Re-enqueuing does not change a row's due state, so we cannot page by
        # re-querying the first rows (we'd loop on the same page). Page by id
        # instead to drain the whole backlog -- e.g. after a prolonged outage.
        due = await db_client.get_due_webhook_deliveries(
            now=datetime.now(UTC), limit=page_size, after_id=after_id
        )
        if not due:
            break
        for delivery in due:
            # A reclaim token (the current lease timestamp) gives this a fresh job
            # id so it is not deduped against the original attempt's completed job
            # -- otherwise a delivered-but-unrecorded row could sit until ARQ's
            # result retention clears.
            reclaim_token = (
                int(delivery.scheduled_for.timestamp()) if delivery.scheduled_for else 0
            )
            await _enqueue_delivery(
                delivery.id,
                attempt_count=delivery.attempt_count,
                reclaim_token=reclaim_token,
            )
        total += len(due)
        after_id = due[-1].id
        if len(due) < page_size:
            break

    if total:
        logger.info(f"Webhook delivery sweep: re-enqueued {total} due deliveries")
