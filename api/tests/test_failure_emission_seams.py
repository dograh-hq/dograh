from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from loguru import logger

from api.errors.failure import ErrorType
from api.services.integrations.registry import run_completion_handlers
from api.services.pipecat.pre_call_fetch import execute_pre_call_fetch
from api.services.workflow.tools.custom_tool import execute_http_tool
from api.tasks.webhook_delivery import deliver_webhook


def _async_client(*, response=None, error=None):
    client = MagicMock()
    if response is not None:
        client.request = AsyncMock(return_value=response)
        client.post = AsyncMock(return_value=response)
    else:
        client.request = AsyncMock(side_effect=error)
        client.post = AsyncMock(side_effect=error)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=context)


@pytest.mark.asyncio
async def test_custom_tool_http_error_is_classified_without_changing_result():
    response = MagicMock(status_code=401, text="Unauthorized")
    response.json.return_value = {"error": "Unauthorized"}
    captured = []
    tool = SimpleNamespace(
        name="Customer Lookup",
        tool_uuid="tool-1",
        definition={
            "config": {
                "method": "POST",
                "url": "https://customer.test/lookup",
            }
        },
    )

    with (
        patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient",
            _async_client(response=response),
        ),
        patch(
            "api.services.workflow.tools.custom_tool.log_failure",
            side_effect=lambda failure, **_context: captured.append(failure),
        ),
    ):
        result = await execute_http_tool(tool, {}, organization_id=42)

    assert result == {
        "status": "success",
        "status_code": 401,
        "data": {"error": "Unauthorized"},
    }
    assert captured[0].source.value == "tool"
    assert captured[0].type == ErrorType.CONFIG_ERROR
    assert captured[0].code == "custom-http-401"


@pytest.mark.asyncio
async def test_pre_call_fetch_classifies_http_failure_and_still_returns_empty():
    response = MagicMock(status_code=503, text="Unavailable", is_success=False)
    response.json.return_value = {}
    captured = []

    with (
        patch(
            "api.services.pipecat.pre_call_fetch.httpx.AsyncClient",
            _async_client(response=response),
        ),
        patch(
            "api.services.pipecat.pre_call_fetch.log_failure",
            side_effect=lambda failure, **_context: captured.append(failure),
        ),
    ):
        result = await execute_pre_call_fetch(
            url="https://customer.test/context",
            credential_uuid=None,
            call_context_vars={},
            workflow_id=7,
            organization_id=42,
        )

    assert result == {}
    assert captured[0].source.value == "integration"
    assert captured[0].type == ErrorType.PROVIDER_ERROR
    assert captured[0].code == "pre-call-fetch-503"


@pytest.mark.asyncio
async def test_integration_completion_failure_remains_isolated(monkeypatch):
    package = SimpleNamespace(
        name="salesforce",
        run_completion=AsyncMock(side_effect=httpx.ConnectError("connection refused")),
    )
    context = SimpleNamespace(
        workflow_definition={},
        organization_id=42,
        workflow_run_id=88,
    )
    monkeypatch.setattr(
        "api.services.integrations.registry.iter_completion_packages",
        lambda _definition: [(package, [{}])],
    )

    records = []
    sink_id = logger.add(lambda message: records.append(message.record))
    try:
        result = await run_completion_handlers(context=context)
    finally:
        logger.remove(sink_id)

    assert result == {"integration_salesforce": {"error": "completion_handler_failed"}}
    failure_records = [
        record for record in records if "DOGRAH_FAILURE" in str(record["message"])
    ]
    assert len(failure_records) == 1
    assert failure_records[0]["extra"]["error_type"] == ErrorType.PROVIDER_ERROR
    assert failure_records[0]["extra"]["error_code"] == "salesforce-connection"
    assert failure_records[0]["extra"]["exception_type"] == "ConnectError"
    assert (
        "run_completion_handlers" in failure_records[0]["extra"]["exception_traceback"]
    )
    assert failure_records[0]["file"].name == "registry.py"


def _delivery(*, attempt_count):
    return SimpleNamespace(
        id=1,
        workflow_run_id=88,
        organization_id=42,
        attempt_count=attempt_count,
        max_attempts=5,
        http_method="POST",
        credential_uuid=None,
        custom_headers=[],
        delivery_uuid="delivery-uuid",
        endpoint_url="https://customer.test/webhook",
        payload={"event": "done"},
        webhook_name="Final Webhook",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("attempt_count", "expected_logs"), [(0, 0), (4, 1)])
async def test_webhook_failure_logs_only_when_delivery_dead_letters(
    attempt_count, expected_logs
):
    delivery = _delivery(attempt_count=attempt_count)
    db = MagicMock()
    db.claim_webhook_delivery = AsyncMock(return_value=delivery)
    db.get_credential_by_uuid = AsyncMock(return_value=None)
    db.schedule_webhook_delivery_retry = AsyncMock()
    db.mark_webhook_delivery_dead_letter = AsyncMock()
    captured = []

    with (
        patch("api.tasks.webhook_delivery.db_client", db),
        patch(
            "api.tasks.webhook_delivery.httpx.AsyncClient",
            _async_client(error=httpx.ConnectError("connection refused")),
        ),
        patch("api.tasks.webhook_delivery._enqueue_delivery", AsyncMock()),
        patch(
            "api.tasks.webhook_delivery.log_failure",
            side_effect=lambda failure, **_context: captured.append(failure),
        ),
    ):
        await deliver_webhook(None, delivery.id)

    assert len(captured) == expected_logs
    if expected_logs:
        assert captured[0].type == ErrorType.PROVIDER_ERROR
        assert captured[0].code == "webhook-connection"
