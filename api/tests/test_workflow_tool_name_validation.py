from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.tool_name_validation import (
    validate_workflow_tool_name_collisions,
)


@pytest.mark.asyncio
async def test_duplicate_custom_tool_function_names_are_rejected_per_node():
    definition = {
        "nodes": [
            {
                "id": "agent-1",
                "data": {"tool_uuids": ["tool-2", "tool-1"]},
            }
        ],
        "edges": [],
    }
    tools = [
        SimpleNamespace(
            tool_uuid="tool-1",
            name="Transfer Callback",
            category="transfer_call",
        ),
        SimpleNamespace(
            tool_uuid="tool-2",
            name="transfer-callback",
            category="http_api",
        ),
    ]

    with patch(
        "api.services.workflow.tool_name_validation.db_client.get_tools_by_uuids",
        AsyncMock(return_value=tools),
    ) as get_tools_mock:
        errors = await validate_workflow_tool_name_collisions(definition, 11)

    assert errors == [
        {
            "kind": "node",
            "id": "agent-1",
            "field": "data.tool_uuids",
            "message": (
                'Custom tools "Transfer Callback", "transfer-callback" generate '
                'duplicate LLM tool name "transfer_callback" on this node. Rename '
                "one of the tools."
            ),
        }
    ]
    get_tools_mock.assert_awaited_once_with(["tool-1", "tool-2"], 11)


@pytest.mark.asyncio
async def test_custom_tool_collision_is_scoped_to_its_node():
    definition = {
        "nodes": [
            {
                "id": "agent-1",
                "data": {"tool_uuids": ["tool-1"]},
            },
            {
                "id": "agent-2",
                "data": {},
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": "agent-2",
                "target": "end-1",
                "data": {"label": "transfer callback"},
            }
        ],
    }
    tool = SimpleNamespace(
        tool_uuid="tool-1",
        name="Transfer Callback",
        category="transfer_call",
    )

    with patch(
        "api.services.workflow.tool_name_validation.db_client.get_tools_by_uuids",
        AsyncMock(return_value=[tool]),
    ):
        errors = await validate_workflow_tool_name_collisions(definition, 11)

    assert errors == []
