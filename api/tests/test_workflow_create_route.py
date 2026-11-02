from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.enums import CallType
from api.routes.workflow import router
from api.services.auth.depends import get_user


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1,
        provider_id="provider-1",
        selected_organization_id=11,
    )
    return app


@pytest.mark.parametrize("action", ["validate", "publish"])
@pytest.mark.parametrize("schema_error", [None, "start_node", "other_node", "edge"])
@pytest.mark.parametrize(
    "recording_fields",
    [
        {},
        {"greeting_recording_id": None},
        {"greeting_recording_id": ""},
        {"greeting_recording_id": "  "},
    ],
)
def test_validate_workflow_reports_missing_greeting_recording_on_start_node(
    action, schema_error, recording_fields
):
    definition = {
        "nodes": [
            {
                "id": "start-1",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "Greet the caller",
                    "greeting_type": "audio",
                    **recording_fields,
                },
            },
            {
                "id": "end-1",
                "type": "endCall",
                "position": {"x": 0, "y": 100},
                "data": {"name": "End", "prompt": "Say goodbye"},
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": "start-1",
                "target": "end-1",
                "data": {"label": "End", "condition": "Conversation finished"},
            }
        ],
    }
    if schema_error == "edge":
        definition["edges"][0]["data"]["condition"] = ""
        expected_schema_error = ("edge", "edge-1", "data.condition")
    elif schema_error:
        node_index = 0 if schema_error == "start_node" else 1
        definition["nodes"][node_index]["position"]["x"] = "invalid"
        expected_schema_error = (
            "node",
            definition["nodes"][node_index]["id"],
            "position.x",
        )
    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_workflow = AsyncMock(return_value=SimpleNamespace(id=33))
        mock_db.get_draft_version = AsyncMock(
            return_value=SimpleNamespace(
                workflow_json=definition, workflow_configurations={}
            )
        )
        mock_db.publish_workflow_draft = AsyncMock()
        response = TestClient(_make_test_app()).post(f"/workflow/33/{action}")
        mock_db.publish_workflow_draft.assert_not_awaited()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["is_valid"] is False
    assert len(detail["errors"]) == (2 if schema_error else 1)
    if schema_error:
        assert expected_schema_error in {
            (error["kind"], error["id"], error["field"]) for error in detail["errors"]
        }
    greeting_errors = [
        error
        for error in detail["errors"]
        if error["field"] == "data.greeting_recording_id"
    ]
    assert len(greeting_errors) == 1
    error = greeting_errors[0]
    assert error["kind"] == "node"
    assert error["id"] == "start-1"
    assert error["field"] == "data.greeting_recording_id"
    assert "select a greeting recording" in error["message"].lower()


def test_update_workflow_drops_retired_inline_model_keys_before_db_write():
    """Old clients may echo inline model keys; the catalog binding is the only
    model setting a save may carry."""
    published = SimpleNamespace(
        workflow_json={},
        workflow_configurations={"max_call_duration": 60},
        template_context_variables={},
        version_number=1,
        status="published",
    )
    workflow = SimpleNamespace(
        id=33,
        name="Agent",
        status="active",
        created_at=datetime.now(UTC),
        current_definition_id=None,
        call_disposition_codes={},
        released_definition=published,
    )
    client = TestClient(_make_test_app())
    with (
        patch("api.routes.workflow.db_client") as mock_db,
        patch(
            "api.routes.workflow.apply_external_pbx_mapping_policy",
            AsyncMock(side_effect=lambda incoming, **kwargs: incoming),
        ),
    ):
        mock_db.get_draft_version = AsyncMock(return_value=None)
        mock_db.update_workflow = AsyncMock(return_value=workflow)
        response = client.put(
            "/workflow/33",
            json={
                "workflow_configurations": {
                    "max_call_duration": 60,
                    "model_overrides": {"llm": {"model": "anthropic/claude-sonnet-4"}},
                    "model_configuration_v2_override": {"mode": "dograh"},
                },
            },
        )
    assert response.status_code == 200
    assert mock_db.update_workflow.await_args.kwargs["workflow_configurations"] == {
        "max_call_duration": 60
    }


def test_create_workflow_rejects_invalid_trigger_path_before_db_write():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        response = client.post(
            "/workflow/create/definition",
            json={
                "name": "Support Agent",
                "workflow_definition": {
                    "nodes": [
                        {
                            "id": "trigger-1",
                            "type": "trigger",
                            "data": {"trigger_path": "support/west"},
                        }
                    ],
                    "edges": [],
                },
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["is_valid"] is False
    assert detail["errors"][0]["field"] == "data.trigger_path"
    assert "single URL path segment" in detail["errors"][0]["message"]
    assert mock_db.mock_calls == []


def test_create_workflow_rejects_duplicate_api_triggers_before_db_write():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        response = client.post(
            "/workflow/create/definition",
            json={
                "name": "Support Agent",
                "workflow_definition": {
                    "nodes": [
                        {
                            "id": "trigger-1",
                            "type": "trigger",
                            "data": {"trigger_path": "support_west"},
                        },
                        {
                            "id": "trigger-2",
                            "type": "trigger",
                            "data": {"trigger_path": "support_east"},
                        },
                    ],
                    "edges": [],
                },
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["is_valid"] is False
    assert detail["errors"][0]["kind"] == "workflow"
    assert "at most one API Trigger" in detail["errors"][0]["message"]
    assert mock_db.mock_calls == []


def test_update_workflow_rejects_duplicate_transition_tools_before_db_write():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        response = client.put(
            "/workflow/33",
            json={
                "workflow_definition": {
                    "nodes": [],
                    "edges": [
                        {
                            "id": "edge-1",
                            "source": "agent-1",
                            "target": "end-1",
                            "data": {
                                "label": "Go to sales",
                                "condition": "Caller wants sales.",
                            },
                        },
                        {
                            "id": "edge-2",
                            "source": "agent-1",
                            "target": "end-2",
                            "data": {
                                "label": "go-to-sales",
                                "condition": "Caller asks for sales.",
                            },
                        },
                    ],
                }
            },
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["is_valid"] is False
    assert {error["id"] for error in detail["errors"]} == {"edge-1", "edge-2"}
    assert all(error["kind"] == "edge" for error in detail["errors"])
    assert all(error["field"] == "data.label" for error in detail["errors"])
    assert all(
        'Transition tool name "go_to_sales" is duplicated' in error["message"]
        for error in detail["errors"]
    )
    assert mock_db.mock_calls == []


def test_update_workflow_rejects_transition_custom_tool_collision_before_db_write():
    app = _make_test_app()
    client = TestClient(app)
    tool = SimpleNamespace(
        tool_uuid="tool-uuid-1",
        name="Transfer Callback",
        category="transfer_call",
    )

    with (
        patch("api.routes.workflow.db_client") as mock_db,
        patch(
            "api.services.workflow.tool_name_validation.db_client.get_tools_by_uuids",
            AsyncMock(return_value=[tool]),
        ) as get_tools_mock,
    ):
        response = client.put(
            "/workflow/33",
            json={
                "workflow_definition": {
                    "nodes": [
                        {
                            "id": "agent-1",
                            "type": "agentNode",
                            "data": {
                                "name": "Agent",
                                "tool_uuids": ["tool-uuid-1"],
                            },
                        }
                    ],
                    "edges": [
                        {
                            "id": "edge-1",
                            "source": "agent-1",
                            "target": "end-1",
                            "data": {
                                "label": "transfer callback",
                                "condition": "Caller requested a callback.",
                            },
                        }
                    ],
                }
            },
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["is_valid"] is False
    assert detail["errors"] == [
        {
            "kind": "edge",
            "id": "edge-1",
            "field": "data.label",
            "message": (
                'Transition tool name "transfer_callback" conflicts with custom '
                'tool "Transfer Callback" attached to this node. Use a unique '
                "edge label or rename the custom tool."
            ),
        }
    ]
    get_tools_mock.assert_awaited_once_with(["tool-uuid-1"], 11)
    assert mock_db.mock_calls == []


def test_create_workflow_run_uses_draft_and_template_context():
    app = _make_test_app()
    client = TestClient(app)

    workflow = SimpleNamespace(
        id=33,
        released_definition=SimpleNamespace(
            id=77,
            template_context_variables={"name": "published"},
        ),
        current_definition=None,
        template_context_variables={"name": "workflow"},
    )
    draft = SimpleNamespace(
        id=88,
        template_context_variables={"name": "draft", "draft_only": "kept"},
    )
    run = SimpleNamespace(
        id=501,
        workflow_id=workflow.id,
        name="WR-test",
        mode="smallwebrtc",
        created_at=datetime.now(UTC),
        definition_id=draft.id,
        initial_context={"name": "draft", "draft_only": "kept"},
        gathered_context={},
    )

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_draft_version = AsyncMock(return_value=draft)
        mock_db.create_workflow_run = AsyncMock(return_value=run)

        response = client.post(
            f"/workflow/{workflow.id}/runs",
            json={"name": "WR-test", "mode": "smallwebrtc"},
        )

    assert response.status_code == 200
    mock_db.get_draft_version.assert_awaited_once_with(workflow.id)
    create_kwargs = mock_db.create_workflow_run.await_args.kwargs
    assert create_kwargs["definition_id"] == draft.id
    assert create_kwargs["initial_context"] == {
        "name": "draft",
        "draft_only": "kept",
        "direction": "inbound",
    }
    assert create_kwargs["call_type"] == CallType.INBOUND


def test_create_workflow_webrtc_run_can_simulate_outbound_from_template_context():
    app = _make_test_app()
    client = TestClient(app)

    workflow = SimpleNamespace(id=33, current_definition=None)
    draft = SimpleNamespace(
        id=88,
        template_context_variables={"direction": " OUTBOUND "},
    )
    run = SimpleNamespace(
        id=501,
        workflow_id=workflow.id,
        name="WR-test",
        mode="smallwebrtc",
        created_at=datetime.now(UTC),
        definition_id=draft.id,
        initial_context={"direction": "outbound"},
        gathered_context={},
    )

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_draft_version = AsyncMock(return_value=draft)
        mock_db.create_workflow_run = AsyncMock(return_value=run)

        response = client.post(
            f"/workflow/{workflow.id}/runs",
            json={"name": "WR-test", "mode": "smallwebrtc"},
        )

    assert response.status_code == 200
    create_kwargs = mock_db.create_workflow_run.await_args.kwargs
    assert create_kwargs["call_type"] == CallType.OUTBOUND
    assert create_kwargs["initial_context"]["direction"] == "outbound"
