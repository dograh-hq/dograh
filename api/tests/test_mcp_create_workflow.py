from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.mcp_server.tools.create_workflow import create_workflow


@pytest.mark.asyncio
async def test_create_workflow_rejects_duplicate_api_triggers():
    user = MagicMock()
    user.id = 1
    user.selected_organization_id = 1
    payload = {
        "nodes": [
            {
                "id": "start-1",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {"name": "Start", "prompt": "Greet."},
            },
            {
                "id": "trigger-1",
                "type": "trigger",
                "position": {"x": 0, "y": 200},
                "data": {"name": "Trigger A", "trigger_path": "support_west"},
            },
            {
                "id": "trigger-2",
                "type": "trigger",
                "position": {"x": 0, "y": 400},
                "data": {"name": "Trigger B", "trigger_path": "support_east"},
            },
        ],
        "edges": [],
    }

    with (
        patch(
            "api.mcp_server.tools.create_workflow.authenticate_mcp_request",
            AsyncMock(return_value=user),
        ),
        patch(
            "api.mcp_server.tools.create_workflow.parse_code",
            AsyncMock(
                return_value={
                    "ok": True,
                    "workflowName": "duplicate-trigger-test",
                    "workflow": payload,
                }
            ),
        ),
        patch(
            "api.mcp_server.tools.create_workflow.reconcile_positions",
            return_value=payload,
        ),
        patch(
            "api.mcp_server.tools.create_workflow.db_client.create_workflow",
            AsyncMock(),
        ) as create_mock,
    ):
        result = await create_workflow(code="ignored")

    assert result["created"] is False
    assert result["error_code"] == "graph_validation"
    assert "at most one API Trigger" in result["error"]
    create_mock.assert_not_awaited()
