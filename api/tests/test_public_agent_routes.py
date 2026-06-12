from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.public_agent import router


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _active_workflow(*, trigger_path: str | None = None):
    nodes = []
    if trigger_path is not None:
        nodes.append(
            {
                "type": "trigger",
                "data": {"trigger_path": trigger_path},
            }
        )

    return SimpleNamespace(
        id=33,
        user_id=99,
        organization_id=11,
        status="active",
        workflow_uuid="workflow-uuid-123",
        released_definition=SimpleNamespace(
            workflow_json={"nodes": nodes, "edges": []}
        ),
    )


def _provider():
    return SimpleNamespace(
        PROVIDER_NAME="twilio",
        WEBHOOK_ENDPOINT="outbound",
        validate_config=Mock(return_value=True),
        initiate_call=AsyncMock(),
    )


def test_trigger_route_executes_as_workflow_owner():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _active_workflow(trigger_path="trigger-uuid-123")
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.public_agent.db_client") as mock_db,
        patch(
            "api.routes.public_agent.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.public_agent.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.public_agent.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        mock_db.validate_api_key = AsyncMock(
            return_value=SimpleNamespace(id=7, organization_id=11, created_by=22)
        )
        mock_db.get_agent_trigger_by_path = AsyncMock(
            return_value=SimpleNamespace(
                workflow_id=workflow.id,
                organization_id=11,
                state="active",
            )
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=501))

        response = client.post(
            "/public/agent/trigger-uuid-123",
            headers={"X-API-Key": "test-api-key"},
            json={"phone_number": "+15551234567"},
        )

    assert response.status_code == 200
    quota_mock.assert_awaited_once_with(
        workflow_id=workflow.id,
        workflow_run_id=501,
    )
    mock_db.get_workflow.assert_awaited_once_with(workflow.id, organization_id=11)

    create_kwargs = mock_db.create_workflow_run.await_args.kwargs
    assert create_kwargs["workflow_id"] == workflow.id
    assert create_kwargs["user_id"] == workflow.user_id
    assert create_kwargs["organization_id"] == workflow.organization_id
    assert create_kwargs["initial_context"]["agent_uuid"] == "trigger-uuid-123"
    assert create_kwargs["initial_context"]["agent_identifier"] == "trigger-uuid-123"
    assert create_kwargs["initial_context"]["agent_identifier_type"] == "trigger_path"
    assert create_kwargs["initial_context"]["workflow_uuid"] == workflow.workflow_uuid
    assert create_kwargs["initial_context"]["api_key_id"] == 7
    assert create_kwargs["initial_context"]["api_key_created_by"] == 22

    initiate_kwargs = provider.initiate_call.await_args.kwargs
    assert initiate_kwargs["workflow_id"] == workflow.id
    assert initiate_kwargs["user_id"] == workflow.user_id


def test_workflow_uuid_route_uses_scoped_lookup_and_shared_execution():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _active_workflow()
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.public_agent.db_client") as mock_db,
        patch(
            "api.routes.public_agent.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.public_agent.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.public_agent.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        mock_db.validate_api_key = AsyncMock(
            return_value=SimpleNamespace(id=8, organization_id=11, created_by=22)
        )
        mock_db.get_workflow_by_uuid = AsyncMock(return_value=workflow)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=601))

        response = client.post(
            f"/public/agent/workflow/{workflow.workflow_uuid}",
            headers={"X-API-Key": "test-api-key"},
            json={"phone_number": "+15551234567"},
        )

    assert response.status_code == 200
    mock_db.get_workflow_by_uuid.assert_awaited_once_with(
        workflow.workflow_uuid,
        11,
    )
    assert not mock_db.get_agent_trigger_by_path.called

    create_kwargs = mock_db.create_workflow_run.await_args.kwargs
    assert create_kwargs["user_id"] == workflow.user_id
    assert (
        create_kwargs["initial_context"]["agent_identifier"] == workflow.workflow_uuid
    )
    assert create_kwargs["initial_context"]["agent_identifier_type"] == "workflow_uuid"
    assert "agent_uuid" not in create_kwargs["initial_context"]


def test_workflow_uuid_route_rejects_archived_workflows():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _active_workflow()
    workflow.status = "archived"

    with patch("api.routes.public_agent.db_client") as mock_db:
        mock_db.validate_api_key = AsyncMock(
            return_value=SimpleNamespace(id=9, organization_id=11, created_by=22)
        )
        mock_db.get_workflow_by_uuid = AsyncMock(return_value=workflow)

        response = client.post(
            f"/public/agent/workflow/{workflow.workflow_uuid}",
            headers={"X-API-Key": "test-api-key"},
            json={"phone_number": "+15551234567"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflow is not active"
    assert not mock_db.create_workflow_run.called
