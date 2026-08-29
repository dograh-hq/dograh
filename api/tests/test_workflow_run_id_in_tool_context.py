"""The workflow run id must reach in-call tools.

An HTTP tool that fires mid-call (booking an appointment, adding a number to a
do-not-call list) writes a record that an external backend later has to
correlate back to the run. Post-run webhook nodes already render
``workflow_run_id``; in-call tools did not (#690), so it resolved to nothing.

Note the two spellings are not interchangeable, and that is by design:
``initial_context`` is spread at the top level of the URL and preset-parameter
contexts, but the body context reserves the top level for LLM/preset arguments.
So a body template has to say ``{{initial_context.workflow_run_id}}``.
"""

from dataclasses import dataclass
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.services.workflow.initial_context import (
    RESERVED_INITIAL_CONTEXT_KEYS,
    WORKFLOW_RUN_ID_CONTEXT_KEY,
    merge_external_initial_context,
)
from api.services.workflow.tools.custom_tool import execute_http_tool

RUN_ID = 4242


@dataclass
class _ToolModel:
    tool_uuid: str
    name: str
    description: str
    definition: Dict[str, Any]
    category: str = "http_api"


def _tool(config: Dict[str, Any]) -> _ToolModel:
    return _ToolModel(
        tool_uuid="tool-uuid-run-id",
        name="Create Booking",
        description="Create a booking",
        definition={"schema_version": 1, "type": "http_api", "config": config},
    )


@pytest.fixture
def http_client():
    """Patch httpx so the tool records its request instead of sending one."""
    with patch(
        "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
    ) as client_class:
        client = AsyncMock()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"ok": True}
        client.request.return_value = response
        client_class.return_value.__aenter__.return_value = client
        yield client


@pytest.mark.asyncio
async def test_run_id_renders_in_the_url_template(http_client):
    tool = _tool(
        {
            "method": "POST",
            "url": "https://api.example.com/runs/{{workflow_run_id}}/bookings",
            "timeout_ms": 5000,
        }
    )

    result = await execute_http_tool(
        tool, {}, call_context_vars={WORKFLOW_RUN_ID_CONTEXT_KEY: RUN_ID}
    )

    assert result["status"] == "success"
    assert (
        http_client.request.call_args.kwargs["url"]
        == "https://api.example.com/runs/4242/bookings"
    )


@pytest.mark.asyncio
async def test_run_id_renders_in_the_body_template(http_client):
    tool = _tool(
        {
            "method": "POST",
            "url": "https://api.example.com/bookings",
            "timeout_ms": 5000,
            "body_template": {"run_id": "{{initial_context.workflow_run_id}}"},
        }
    )

    result = await execute_http_tool(
        tool, {}, call_context_vars={WORKFLOW_RUN_ID_CONTEXT_KEY: RUN_ID}
    )

    assert result["status"] == "success"
    assert http_client.request.call_args.kwargs["json"] == {"run_id": RUN_ID}


@pytest.mark.asyncio
async def test_run_id_renders_in_preset_parameters(http_client):
    tool = _tool(
        {
            "method": "POST",
            "url": "https://api.example.com/bookings",
            "timeout_ms": 5000,
            "preset_parameters": [
                {"name": "run_id", "value_template": "{{workflow_run_id}}"}
            ],
        }
    )

    result = await execute_http_tool(
        tool, {}, call_context_vars={WORKFLOW_RUN_ID_CONTEXT_KEY: RUN_ID}
    )

    assert result["status"] == "success"
    assert http_client.request.call_args.kwargs["json"] == {"run_id": "4242"}


@pytest.mark.asyncio
async def test_without_the_run_id_the_tool_cannot_reference_the_run(http_client):
    """The #690 behavior: the templates collapse and no run can be identified."""
    tool = _tool(
        {
            "method": "POST",
            "url": "https://api.example.com/runs/{{workflow_run_id}}/bookings",
            "timeout_ms": 5000,
            "body_template": {"run_id": "{{initial_context.workflow_run_id}}"},
        }
    )

    result = await execute_http_tool(tool, {}, call_context_vars={})

    # A URL template variable with no value fails loudly rather than posting to
    # a mangled path, so the request is never sent at all.
    assert result["status"] == "error"
    assert "URL template rendering failed" in result["error"]
    http_client.request.assert_not_called()


@pytest.mark.asyncio
async def test_body_only_tool_silently_loses_the_run_id_without_it(http_client):
    """Without a URL template there is no loud failure - just an empty value."""
    tool = _tool(
        {
            "method": "POST",
            "url": "https://api.example.com/bookings",
            "timeout_ms": 5000,
            "body_template": {"run_id": "{{initial_context.workflow_run_id}}"},
        }
    )

    await execute_http_tool(tool, {}, call_context_vars={})

    assert http_client.request.call_args.kwargs["json"] == {"run_id": ""}


def test_run_id_is_reserved_against_external_context():
    assert WORKFLOW_RUN_ID_CONTEXT_KEY in RESERVED_INITIAL_CONTEXT_KEYS

    merged = merge_external_initial_context(
        {WORKFLOW_RUN_ID_CONTEXT_KEY: RUN_ID, "customer_name": "Before"},
        {WORKFLOW_RUN_ID_CONTEXT_KEY: 9999, "customer_name": "After"},
    )

    assert merged == {WORKFLOW_RUN_ID_CONTEXT_KEY: RUN_ID, "customer_name": "After"}


def test_run_id_cannot_be_introduced_by_external_context():
    assert merge_external_initial_context(
        {}, {WORKFLOW_RUN_ID_CONTEXT_KEY: 9999, "customer_name": "Ada"}
    ) == {"customer_name": "Ada"}
