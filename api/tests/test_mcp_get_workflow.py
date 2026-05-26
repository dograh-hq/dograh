from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.mcp_server.tools.workflows import get_workflow


@pytest.fixture
def authed_user() -> MagicMock:
    user = MagicMock()
    user.selected_organization_id = 1
    return user


def _workflow() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        name="Support Agent",
        status="active",
        released_definition=SimpleNamespace(
            workflow_json={"nodes": [{"id": "published"}], "edges": []},
            version_number=3,
        ),
        workflow_definition={"nodes": [{"id": "legacy"}], "edges": []},
    )


@pytest.mark.asyncio
async def test_get_workflow_returns_draft_sdk_view(authed_user: MagicMock):
    workflow = _workflow()
    draft = SimpleNamespace(
        workflow_json={"nodes": [{"id": "draft"}], "edges": []},
        version_number=4,
    )

    with (
        patch(
            "api.mcp_server.tools.workflows.authenticate_mcp_request",
            AsyncMock(return_value=authed_user),
        ),
        patch(
            "api.mcp_server.tools.workflows.db_client.get_workflow",
            AsyncMock(return_value=workflow),
        ),
        patch(
            "api.mcp_server.tools._workflow_projection.db_client.get_draft_version",
            AsyncMock(return_value=draft),
        ),
        patch(
            "api.mcp_server.tools._workflow_projection.generate_code",
            AsyncMock(
                return_value='const wf = new Workflow({ name: "Support Agent" });'
            ),
        ) as generate_code_mock,
    ):
        result = await get_workflow(workflow_id=workflow.id)

    assert result == {
        "id": 7,
        "name": "Support Agent",
        "status": "active",
        "version": "draft",
        "version_number": 4,
        "code": 'const wf = new Workflow({ name: "Support Agent" });',
    }
    generate_code_mock.assert_awaited_once_with(
        draft.workflow_json, workflow_name="Support Agent"
    )


@pytest.mark.asyncio
async def test_get_workflow_falls_back_to_published_sdk_view(authed_user: MagicMock):
    workflow = _workflow()

    with (
        patch(
            "api.mcp_server.tools.workflows.authenticate_mcp_request",
            AsyncMock(return_value=authed_user),
        ),
        patch(
            "api.mcp_server.tools.workflows.db_client.get_workflow",
            AsyncMock(return_value=workflow),
        ),
        patch(
            "api.mcp_server.tools._workflow_projection.db_client.get_draft_version",
            AsyncMock(return_value=None),
        ),
        patch(
            "api.mcp_server.tools._workflow_projection.generate_code",
            AsyncMock(
                return_value='const wf = new Workflow({ name: "Support Agent" });'
            ),
        ),
    ):
        result = await get_workflow(workflow_id=workflow.id)

    assert result["version"] == "published"
    assert result["version_number"] == 3
