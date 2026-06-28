from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api.services.workflow.dto import WebhookNodeData
from api.tasks.run_integrations import (
    _build_webhook_payload,
    _enqueue_webhook_delivery,
)
from api.tasks.webhook_delivery import deliver_webhook


# ---------------------------------------------------------------------------
# Payload rendering (call_disposition injection)
# ---------------------------------------------------------------------------


def test_build_webhook_payload_injects_disposition_when_absent():
    """call_disposition is added to the payload when the template omits it."""
    webhook = WebhookNodeData(
        name="Test Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        payload_template={"event": "call_done"},
    )
    render_context = {"gathered_context": {"call_disposition": "no-answer"}}

    payload = _build_webhook_payload(webhook, render_context)

    assert payload == {"event": "call_done", "call_disposition": "no-answer"}


def test_build_webhook_payload_preserves_template_disposition():
    """A disposition key set explicitly in the template is not overwritten."""
    webhook = WebhookNodeData(
        name="Test Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        payload_template={"call_disposition": "custom-from-template"},
    )
    render_context = {"gathered_context": {"call_disposition": "no-answer"}}

    payload = _build_webhook_payload(webhook, render_context)

    assert payload["call_disposition"] == "custom-from-template"


def test_build_webhook_payload_empty_disposition_when_context_missing():
    """Missing gathered_context values fall back to an empty string, not omission."""
    webhook = WebhookNodeData(
        name="Test Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        payload_template={},
    )

    payload = _build_webhook_payload(webhook, {})

    assert payload == {"call_disposition": ""}


# ---------------------------------------------------------------------------
# Enqueue: persist a delivery row and schedule the first send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_webhook_delivery_persists_and_enqueues():
    created = SimpleNamespace(id=42, delivery_uuid="uuid-42")
    db = MagicMock()
    db.create_webhook_delivery = AsyncMock(return_value=created)
    enqueue = AsyncMock()

    webhook = WebhookNodeData(
        name="Final Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        http_method="POST",
        payload_template={"event": "call_done"},
    )

    with (
        patch("api.tasks.run_integrations.db_client", db),
        patch("api.tasks.arq.enqueue_job", enqueue),
    ):
        await _enqueue_webhook_delivery(
            webhook_data=webhook,
            render_context={"gathered_context": {"call_disposition": "user_hangup"}},
            organization_id=7,
            workflow_run_id=9,
        )

    db.create_webhook_delivery.assert_awaited_once()
    kwargs = db.create_webhook_delivery.call_args.kwargs
    assert kwargs["workflow_run_id"] == 9
    assert kwargs["organization_id"] == 7
    assert kwargs["endpoint_url"] == "https://example.com/hook"
    assert kwargs["payload"]["call_disposition"] == "user_hangup"

    enqueue.assert_awaited_once()
    # Deterministic job id for the first attempt (dedup-safe).
    assert enqueue.call_args.kwargs["_job_id"] == "webhook-delivery-42-0"


@pytest.mark.asyncio
async def test_enqueue_webhook_delivery_skips_disabled():
    db = MagicMock()
    db.create_webhook_delivery = AsyncMock()

    webhook = WebhookNodeData(
        name="Disabled",
        enabled=False,
        endpoint_url="https://example.com/hook",
        payload_template={},
    )

    with patch("api.tasks.run_integrations.db_client", db):
        await _enqueue_webhook_delivery(
            webhook_data=webhook,
            render_context={},
            organization_id=1,
            workflow_run_id=1,
        )

    db.create_webhook_delivery.assert_not_called()


# ---------------------------------------------------------------------------
# Delivery task: send, retry, dead-letter
# ---------------------------------------------------------------------------


def _fake_delivery(**overrides):
    base = dict(
        id=1,
        delivery_uuid="uuid-1",
        workflow_run_id=9,
        organization_id=7,
        webhook_name="Final Webhook",
        endpoint_url="https://example.com/hook",
        http_method="POST",
        payload={"event": "call_done"},
        custom_headers=None,
        credential_uuid=None,
        status="pending",
        attempt_count=0,
        max_attempts=5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_httpx(*, raise_request_error=None, status_error=None, status_code=200):
    """Patch target for httpx.AsyncClient used by the delivery task."""
    response = MagicMock()
    response.status_code = status_code
    response.text = "body"
    if status_error is not None:
        response.raise_for_status = MagicMock(side_effect=status_error)
    else:
        response.raise_for_status = MagicMock()

    async def _request(**kwargs):
        if raise_request_error is not None:
            raise raise_request_error
        return response

    client = MagicMock()
    client.request = AsyncMock(side_effect=_request)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _delivery_db(delivery):
    db = MagicMock()
    db.get_webhook_delivery = AsyncMock(return_value=delivery)
    db.get_credential_by_uuid = AsyncMock(return_value=None)
    db.mark_webhook_delivery_succeeded = AsyncMock()
    db.schedule_webhook_delivery_retry = AsyncMock()
    db.mark_webhook_delivery_dead_letter = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_deliver_webhook_success():
    delivery = _fake_delivery()
    db = _delivery_db(delivery)

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch("api.tasks.webhook_delivery.httpx.AsyncClient", _mock_httpx()),
    ):
        await deliver_webhook(None, delivery.id)

    db.mark_webhook_delivery_succeeded.assert_awaited_once_with(1, 1, 200)
    db.schedule_webhook_delivery_retry.assert_not_called()
    db.mark_webhook_delivery_dead_letter.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_webhook_transient_error_schedules_retry():
    delivery = _fake_delivery(attempt_count=0)
    db = _delivery_db(delivery)
    enqueue = AsyncMock()

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch(
            "api.tasks.webhook_delivery.httpx.AsyncClient",
            _mock_httpx(raise_request_error=httpx.ConnectTimeout("timed out")),
        ),
        patch("api.tasks.arq.enqueue_job", enqueue),
    ):
        await deliver_webhook(None, delivery.id)

    db.schedule_webhook_delivery_retry.assert_awaited_once()
    assert db.schedule_webhook_delivery_retry.call_args.kwargs["attempt_count"] == 1
    db.mark_webhook_delivery_dead_letter.assert_not_called()
    # Re-enqueued with a deferral and the next attempt's job id.
    enqueue.assert_awaited_once()
    assert enqueue.call_args.kwargs["_job_id"] == "webhook-delivery-1-1"
    assert enqueue.call_args.kwargs["_defer_by"] > 0


@pytest.mark.asyncio
async def test_deliver_webhook_permanent_4xx_dead_letters():
    delivery = _fake_delivery()
    db = _delivery_db(delivery)
    resp = MagicMock(status_code=401, text="Unauthorized")
    status_error = httpx.HTTPStatusError("401", request=MagicMock(), response=resp)

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch(
            "api.tasks.webhook_delivery.httpx.AsyncClient",
            _mock_httpx(status_error=status_error, status_code=401),
        ),
    ):
        await deliver_webhook(None, delivery.id)

    db.mark_webhook_delivery_dead_letter.assert_awaited_once()
    db.schedule_webhook_delivery_retry.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_webhook_retryable_5xx_schedules_retry():
    delivery = _fake_delivery()
    db = _delivery_db(delivery)
    enqueue = AsyncMock()
    resp = MagicMock(status_code=503, text="unavailable")
    status_error = httpx.HTTPStatusError("503", request=MagicMock(), response=resp)

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch(
            "api.tasks.webhook_delivery.httpx.AsyncClient",
            _mock_httpx(status_error=status_error, status_code=503),
        ),
        patch("api.tasks.arq.enqueue_job", enqueue),
    ):
        await deliver_webhook(None, delivery.id)

    db.schedule_webhook_delivery_retry.assert_awaited_once()
    db.mark_webhook_delivery_dead_letter.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_webhook_exhausted_attempts_dead_letters():
    # attempt_count=4 -> this is attempt 5 == max_attempts, so no further retry.
    delivery = _fake_delivery(attempt_count=4, max_attempts=5)
    db = _delivery_db(delivery)

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch(
            "api.tasks.webhook_delivery.httpx.AsyncClient",
            _mock_httpx(raise_request_error=httpx.ConnectError("boom")),
        ),
    ):
        await deliver_webhook(None, delivery.id)

    db.mark_webhook_delivery_dead_letter.assert_awaited_once()
    assert db.mark_webhook_delivery_dead_letter.call_args.args[1] == 5
    db.schedule_webhook_delivery_retry.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_webhook_idempotent_when_not_pending():
    delivery = _fake_delivery(status="succeeded")
    db = _delivery_db(delivery)
    httpx_mock = _mock_httpx()

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch("api.tasks.webhook_delivery.httpx.AsyncClient", httpx_mock),
    ):
        await deliver_webhook(None, delivery.id)

    httpx_mock.assert_not_called()
    db.mark_webhook_delivery_succeeded.assert_not_called()
