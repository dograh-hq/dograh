from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from api.db.models import (
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
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
    db.create_webhook_delivery = AsyncMock(return_value=(created, True))
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
            webhook_node_id="node-1",
        )

    db.create_webhook_delivery.assert_awaited_once()
    kwargs = db.create_webhook_delivery.call_args.kwargs
    assert kwargs["workflow_run_id"] == 9
    assert kwargs["organization_id"] == 7
    assert kwargs["endpoint_url"] == "https://example.com/hook"
    assert kwargs["payload"]["call_disposition"] == "user_hangup"
    assert kwargs["webhook_node_id"] == "node-1"

    enqueue.assert_awaited_once()
    # Deterministic job id for the first attempt (dedup-safe).
    assert enqueue.call_args.kwargs["_job_id"] == "webhook-delivery-42-0"


@pytest.mark.asyncio
async def test_enqueue_webhook_delivery_idempotent_does_not_reenqueue():
    # A retried run gets the existing row back (created=False) -> no second send.
    existing = SimpleNamespace(id=42, delivery_uuid="uuid-42")
    db = MagicMock()
    db.create_webhook_delivery = AsyncMock(return_value=(existing, False))
    enqueue = AsyncMock()

    webhook = WebhookNodeData(
        name="Final Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        payload_template={"event": "call_done"},
    )

    with (
        patch("api.tasks.run_integrations.db_client", db),
        patch("api.tasks.arq.enqueue_job", enqueue),
    ):
        await _enqueue_webhook_delivery(
            webhook_data=webhook,
            render_context={},
            organization_id=7,
            workflow_run_id=9,
            webhook_node_id="node-1",
        )

    db.create_webhook_delivery.assert_awaited_once()
    enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_webhook_delivery_drops_secret_custom_headers():
    created = SimpleNamespace(id=1, delivery_uuid="u")
    db = MagicMock()
    db.create_webhook_delivery = AsyncMock(return_value=(created, True))

    webhook = WebhookNodeData(
        name="Final Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        payload_template={},
        custom_headers=[
            {"key": "Authorization", "value": "Bearer secret-token"},
            {"key": "X-Custom-Auth-Token", "value": "abc"},  # variant -> dropped
            {"key": "X-Idempotency-Key", "value": "idem-1"},  # benign -> kept
            {"key": "X-Source", "value": "dograh"},
        ],
    )

    with (
        patch("api.tasks.run_integrations.db_client", db),
        patch("api.tasks.arq.enqueue_job", AsyncMock()),
    ):
        await _enqueue_webhook_delivery(
            webhook_data=webhook,
            render_context={},
            organization_id=1,
            workflow_run_id=1,
            webhook_node_id="n",
        )

    persisted = db.create_webhook_delivery.call_args.kwargs["custom_headers"]
    keys = {h["key"] for h in persisted}
    assert "Authorization" not in keys  # secret dropped, not stored in plaintext
    assert "X-Custom-Auth-Token" not in keys  # variant secret also dropped
    assert "X-Idempotency-Key" in keys  # benign 'key' header NOT a false positive
    assert "X-Source" in keys  # non-secret header kept


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
            webhook_node_id="n",
        )

    db.create_webhook_delivery.assert_not_called()


@pytest.mark.asyncio
async def test_get_workflow_run_with_context_uses_workflow_org(
    async_session, db_session
):
    run_org = OrganizationModel(provider_id=f"run-org-{uuid4()}")
    selected_org = OrganizationModel(provider_id=f"selected-org-{uuid4()}")
    async_session.add_all([run_org, selected_org])
    await async_session.flush()

    user = UserModel(
        provider_id=f"user-{uuid4()}",
        selected_organization_id=selected_org.id,
    )
    async_session.add(user)
    await async_session.flush()

    workflow = WorkflowModel(
        name="Webhook Workflow",
        user_id=user.id,
        organization_id=run_org.id,
        workflow_definition={"nodes": [], "edges": []},
        template_context_variables={},
    )
    async_session.add(workflow)
    await async_session.flush()

    workflow_run = WorkflowRunModel(
        name="Webhook Run",
        workflow_id=workflow.id,
        mode="test",
    )
    async_session.add(workflow_run)
    await async_session.flush()

    _, organization_id = await db_session.get_workflow_run_with_context(workflow_run.id)

    assert organization_id == run_org.id
    assert organization_id != selected_org.id


@pytest.mark.asyncio
async def test_create_webhook_delivery_rejects_org_mismatch(async_session, db_session):
    run_org = OrganizationModel(provider_id=f"run-org-{uuid4()}")
    wrong_org = OrganizationModel(provider_id=f"wrong-org-{uuid4()}")
    async_session.add_all([run_org, wrong_org])
    await async_session.flush()

    user = UserModel(
        provider_id=f"user-{uuid4()}",
        selected_organization_id=wrong_org.id,
    )
    async_session.add(user)
    await async_session.flush()

    workflow = WorkflowModel(
        name="Webhook Workflow",
        user_id=user.id,
        organization_id=run_org.id,
        workflow_definition={"nodes": [], "edges": []},
        template_context_variables={},
    )
    async_session.add(workflow)
    await async_session.flush()

    workflow_run = WorkflowRunModel(
        name="Webhook Run",
        workflow_id=workflow.id,
        mode="test",
    )
    async_session.add(workflow_run)
    await async_session.flush()

    with pytest.raises(ValueError, match="belongs to organization"):
        await db_session.create_webhook_delivery(
            workflow_run_id=workflow_run.id,
            organization_id=wrong_org.id,
            endpoint_url="https://example.com/hook",
            payload={"event": "call_done"},
            max_attempts=5,
            webhook_node_id="node-1",
        )

    delivery, created = await db_session.create_webhook_delivery(
        workflow_run_id=workflow_run.id,
        organization_id=run_org.id,
        endpoint_url="https://example.com/hook",
        payload={"event": "call_done"},
        max_attempts=5,
        webhook_node_id="node-1",
    )

    assert created is True
    assert delivery.organization_id == run_org.id


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
    # The task claims the delivery atomically before sending; a successful claim
    # returns the row.
    db.claim_webhook_delivery = AsyncMock(return_value=delivery)
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
async def test_deliver_webhook_no_op_when_claim_fails():
    # The atomic claim returns None when the delivery is not pending/due or was
    # already claimed by a concurrent worker -> no send, no double-fire.
    delivery = _fake_delivery(status="succeeded")
    db = _delivery_db(delivery)
    db.claim_webhook_delivery = AsyncMock(return_value=None)
    httpx_mock = _mock_httpx()

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch("api.tasks.webhook_delivery.httpx.AsyncClient", httpx_mock),
    ):
        await deliver_webhook(None, delivery.id)

    httpx_mock.assert_not_called()
    db.mark_webhook_delivery_succeeded.assert_not_called()
    db.mark_webhook_delivery_dead_letter.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_webhook_delivered_but_record_failure_does_not_dead_letter():
    # If the HTTP POST is accepted (2xx) but recording success fails (DB blip),
    # the row must NOT be dead-lettered -- it stays pending for the sweeper to
    # reconcile (the receiver dedups the re-send via X-Dograh-Delivery-Id).
    delivery = _fake_delivery()
    db = _delivery_db(delivery)
    db.mark_webhook_delivery_succeeded = AsyncMock(
        side_effect=RuntimeError("db connection blip")
    )

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch("api.tasks.webhook_delivery.httpx.AsyncClient", _mock_httpx()),
    ):
        await deliver_webhook(None, delivery.id)

    db.mark_webhook_delivery_succeeded.assert_awaited_once()
    db.mark_webhook_delivery_dead_letter.assert_not_called()
    db.schedule_webhook_delivery_retry.assert_not_called()
