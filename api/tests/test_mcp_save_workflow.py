"""Integration tests for the `save_workflow` MCP tool.

Mocks `authenticate_mcp_request` and the db_client so tests don't need
a live DB, but exercises the real TS validator subprocess end-to-end —
parse is part of the contract the LLM relies on.

Round-trip and pure-parser tests live in `test_ts_bridge.py`; this file
focuses on the MCP tool's error-routing, version tagging, and DB-call
shape.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.mcp.tools.save_workflow import save_workflow

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node binary not available"
)


# ─── Fixtures & helpers ──────────────────────────────────────────────────


@dataclass
class _FakeDraft:
    version_number: int = 2
    status: str = "draft"


class _FakeWorkflowModel:
    id = 1
    organization_id = 1
    name = "test"
    # reconcile_positions reads whichever of these holds the previous
    # stored workflow JSON; None on all three is fine for a greenfield
    # test and causes reconcile_positions to fall back to the placement
    # heuristic for any new node.
    current_definition = None
    released_definition = None
    workflow_definition = None


@pytest.fixture
def authed_user() -> MagicMock:
    user = MagicMock()
    user.selected_organization_id = 1
    user.id = 1
    return user


@pytest.fixture
def mock_backends(authed_user: MagicMock):
    save_mock = AsyncMock(return_value=_FakeDraft())
    update_mock = AsyncMock(return_value=_FakeWorkflowModel())
    with (
        patch(
            "api.mcp.tools.save_workflow.authenticate_mcp_request",
            AsyncMock(return_value=authed_user),
        ),
        patch(
            "api.mcp.tools.save_workflow.db_client.get_workflow",
            AsyncMock(return_value=_FakeWorkflowModel()),
        ),
        patch(
            "api.mcp.tools.save_workflow.db_client.save_workflow_draft",
            save_mock,
        ),
        patch(
            "api.mcp.tools.save_workflow.db_client.update_workflow",
            update_mock,
        ),
    ):
        yield save_mock, update_mock


def _valid_code(name: str = "tool-test") -> str:
    return f'''import {{ Workflow }} from "@dograh/sdk";
import {{ startCall, endCall }} from "@dograh/sdk/typed";

const wf = new Workflow({{ name: "{name}" }});

const greeting = wf.addTyped(startCall({{ name: "greeting", prompt: "Hi!" }}));
const done     = wf.addTyped(endCall({{ name: "done", prompt: "Bye." }}));

wf.edge(greeting, done, {{ label: "done", condition: "conversation complete" }});
'''


# ─── Happy path ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_saves_draft(mock_backends):
    save_mock, update_mock = mock_backends
    # Match the stored name so the rename branch stays dormant here.
    result = await save_workflow(
        workflow_id=1, code=_valid_code(name=_FakeWorkflowModel.name)
    )
    assert result["saved"] is True
    assert result["workflow_id"] == 1
    assert result["version_number"] == 2
    assert result["status"] == "draft"
    assert result["node_count"] == 2
    assert result["edge_count"] == 1
    assert result["renamed"] is False
    assert result["name"] == _FakeWorkflowModel.name
    save_mock.assert_awaited_once()
    update_mock.assert_not_awaited()
    payload = save_mock.call_args.kwargs["workflow_definition"]
    assert len(payload["nodes"]) == 2
    assert len(payload["edges"]) == 1


@pytest.mark.asyncio
async def test_rename_propagates_to_update_workflow(mock_backends):
    save_mock, update_mock = mock_backends
    result = await save_workflow(workflow_id=1, code=_valid_code(name="renamed"))
    assert result["saved"] is True
    assert result["renamed"] is True
    assert result["name"] == "renamed"
    update_mock.assert_awaited_once()
    kwargs = update_mock.call_args.kwargs
    assert kwargs["workflow_id"] == 1
    assert kwargs["name"] == "renamed"
    assert kwargs["workflow_definition"] is None
    assert kwargs["organization_id"] == 1
    save_mock.assert_awaited_once()


# ─── Parse-stage rejections ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parser_rejects_disallowed_top_level(mock_backends):
    save_mock, update_mock = mock_backends
    code = _valid_code() + "function evil() { return 1; }\n"
    result = await save_workflow(workflow_id=1, code=code)
    assert result["saved"] is False
    assert result["error_code"] == "parse_error"
    save_mock.assert_not_awaited()
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_parser_rejects_unknown_factory(mock_backends):
    save_mock, update_mock = mock_backends
    code = """import { Workflow } from "@dograh/sdk";
const wf = new Workflow({ name: "x" });
const n = wf.addTyped(fakeNode({ name: "x", prompt: "y" }));
"""
    result = await save_workflow(workflow_id=1, code=code)
    assert result["saved"] is False
    assert result["error_code"] == "parse_error"
    assert "Unknown node type" in result["error"]
    save_mock.assert_not_awaited()
    update_mock.assert_not_awaited()


# ─── Validation-stage rejections ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_field_surfaces_validation_error(mock_backends):
    save_mock, update_mock = mock_backends
    code = """import { Workflow } from "@dograh/sdk";
import { startCall } from "@dograh/sdk/typed";
const wf = new Workflow({ name: "x" });
const n = wf.addTyped(startCall({ name: "g", prompt: "hi", promt: "typo" }));
"""
    result = await save_workflow(workflow_id=1, code=code)
    assert result["saved"] is False
    assert result["error_code"] == "validation_error"
    assert "Unknown field" in result["error"]
    save_mock.assert_not_awaited()
    update_mock.assert_not_awaited()


# ─── Graph-stage rejections ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_validation_catches_missing_start_node(mock_backends):
    save_mock, update_mock = mock_backends
    # Only an end node — WorkflowGraph requires exactly one start node.
    code = """import { Workflow } from "@dograh/sdk";
import { endCall } from "@dograh/sdk/typed";
const wf = new Workflow({ name: "orphan" });
const only = wf.addTyped(endCall({ name: "only", prompt: "bye" }));
"""
    result = await save_workflow(workflow_id=1, code=code)
    assert result["saved"] is False
    assert result["error_code"] == "graph_validation"
    save_mock.assert_not_awaited()
    update_mock.assert_not_awaited()


# ─── Workflow not found / unauthorized ───────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_workflow_raises_404(authed_user: MagicMock):
    with (
        patch(
            "api.mcp.tools.save_workflow.authenticate_mcp_request",
            AsyncMock(return_value=authed_user),
        ),
        patch(
            "api.mcp.tools.save_workflow.db_client.get_workflow",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await save_workflow(workflow_id=999, code=_valid_code())
        assert exc_info.value.status_code == 404
