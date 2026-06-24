from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.workflow.dto import WebhookNodeData
from api.tasks.run_integrations import _execute_webhook_node


def _mock_httpx_client(captured: dict):
    """Build a patch target for httpx.AsyncClient that records the request kwargs."""
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()

    async def _request(**kwargs):
        captured.update(kwargs)
        return response

    client = MagicMock()
    client.request = AsyncMock(side_effect=_request)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
async def test_webhook_injects_disposition_when_absent():
    """call_disposition is added to the payload when the template omits it."""
    webhook = WebhookNodeData(
        name="Test Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        payload_template={"event": "call_done"},
    )
    render_context = {"gathered_context": {"call_disposition": "no-answer"}}

    captured: dict = {}
    with patch(
        "api.tasks.run_integrations.httpx.AsyncClient", _mock_httpx_client(captured)
    ):
        ok = await _execute_webhook_node(webhook, render_context, organization_id=1)

    assert ok is True
    assert captured["json"] == {
        "event": "call_done",
        "call_disposition": "no-answer",
    }


@pytest.mark.asyncio
async def test_webhook_preserves_template_disposition():
    """A disposition key set explicitly in the template is not overwritten."""
    webhook = WebhookNodeData(
        name="Test Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        payload_template={"call_disposition": "custom-from-template"},
    )
    render_context = {"gathered_context": {"call_disposition": "no-answer"}}

    captured: dict = {}
    with patch(
        "api.tasks.run_integrations.httpx.AsyncClient", _mock_httpx_client(captured)
    ):
        await _execute_webhook_node(webhook, render_context, organization_id=1)

    assert captured["json"]["call_disposition"] == "custom-from-template"


@pytest.mark.asyncio
async def test_webhook_injects_empty_disposition_when_context_missing():
    """Missing gathered_context values fall back to an empty string, not omission."""
    webhook = WebhookNodeData(
        name="Test Webhook",
        enabled=True,
        endpoint_url="https://example.com/hook",
        payload_template={},
    )

    captured: dict = {}
    with patch(
        "api.tasks.run_integrations.httpx.AsyncClient", _mock_httpx_client(captured)
    ):
        await _execute_webhook_node(webhook, {}, organization_id=1)

    assert captured["json"] == {"call_disposition": ""}
