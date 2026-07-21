from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.public_agent import router
from api.services.call_concurrency import CallConcurrencyLimitError


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
            id=77,
            workflow_json={"nodes": nodes, "edges": []},
            template_context_variables={"name": "published"},
        ),
        current_definition=None,
        template_context_variables={"name": "workflow"},
    )


def _provider():
    return SimpleNamespace(
        PROVIDER_NAME="twilio",
        WEBHOOK_ENDPOINT="outbound",
        validate_config=Mock(return_value=True),
        initiate_call=AsyncMock(
            return_value=SimpleNamespace(
                call_id="CA123",
                status="queued",
                caller_number="+15550000000",
                provider_metadata={"call_id": "CA123"},
            )
        ),
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
        patch("api.routes.public_agent.call_concurrency") as mock_concurrency,
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
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()

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
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/public/agent/trigger-uuid-123",
            headers={"X-API-Key": "test-api-key"},
            json={"phone_number": "+15551234567"},
        )

    assert response.status_code == 200
    quota_mock.assert_awaited_once_with(
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
        workflow_run_id=501,
    )
    mock_concurrency.acquire_org_slot.assert_awaited_once_with(
        workflow.organization_id,
        source="public_agent",
        timeout=0,
    )
    mock_concurrency.bind_workflow_run.assert_awaited_once_with(slot, 501)
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
    assert create_kwargs["definition_id"] == 77
    assert "name" not in create_kwargs["initial_context"]
    assert not mock_db.get_draft_version.called

    initiate_kwargs = provider.initiate_call.await_args.kwargs
    assert initiate_kwargs["workflow_id"] == workflow.id
    # The media websocket URL is keyed on the org, not the workflow owner.
    assert initiate_kwargs["organization_id"] == workflow.organization_id
    mock_db.update_workflow_run.assert_awaited_once_with(
        run_id=501,
        gathered_context={
            "provider": "twilio",
            "triggered_by": "api",
            "call_id": "CA123",
            "trigger_uuid": "trigger-uuid-123",
        },
    )


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
        patch("api.routes.public_agent.call_concurrency") as mock_concurrency,
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
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()

        mock_db.validate_api_key = AsyncMock(
            return_value=SimpleNamespace(id=8, organization_id=11, created_by=22)
        )
        mock_db.get_workflow_by_uuid = AsyncMock(return_value=workflow)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=601))
        mock_db.update_workflow_run = AsyncMock()

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
    mock_concurrency.acquire_org_slot.assert_awaited_once_with(
        workflow.organization_id,
        source="public_agent",
        timeout=0,
    )
    mock_concurrency.bind_workflow_run.assert_awaited_once_with(slot, 601)

    create_kwargs = mock_db.create_workflow_run.await_args.kwargs
    assert create_kwargs["user_id"] == workflow.user_id
    assert (
        create_kwargs["initial_context"]["agent_identifier"] == workflow.workflow_uuid
    )
    assert create_kwargs["initial_context"]["agent_identifier_type"] == "workflow_uuid"
    assert "agent_uuid" not in create_kwargs["initial_context"]
    assert create_kwargs["definition_id"] == 77
    assert "name" not in create_kwargs["initial_context"]
    assert not mock_db.get_draft_version.called
    mock_db.update_workflow_run.assert_awaited_once_with(
        run_id=601,
        gathered_context={
            "provider": "twilio",
            "triggered_by": "api",
            "call_id": "CA123",
        },
    )


def test_trigger_test_route_uses_draft_and_template_context_with_api_override():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _active_workflow(trigger_path="trigger-uuid-123")
    draft = SimpleNamespace(
        id=88,
        workflow_json=workflow.released_definition.workflow_json,
        template_context_variables={"name": "john", "age": 12, "rank": 2},
    )
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.public_agent.db_client") as mock_db,
        patch("api.routes.public_agent.call_concurrency") as mock_concurrency,
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
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()

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
        mock_db.get_draft_version = AsyncMock(return_value=draft)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=501))
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/public/agent/test/trigger-uuid-123",
            headers={"X-API-Key": "test-api-key"},
            json={
                "phone_number": "+15551234567",
                "initial_context": {"name": "tom", "age": 10},
            },
        )

    assert response.status_code == 200
    assert mock_db.get_draft_version.await_count == 2
    create_kwargs = mock_db.create_workflow_run.await_args.kwargs
    assert create_kwargs["definition_id"] == draft.id
    assert create_kwargs["initial_context"]["name"] == "tom"
    assert create_kwargs["initial_context"]["age"] == 10
    assert create_kwargs["initial_context"]["rank"] == 2
    assert create_kwargs["initial_context"]["trigger_mode"] == "test"
    mock_db.update_workflow_run.assert_awaited_once_with(
        run_id=501,
        gathered_context={
            "provider": "twilio",
            "triggered_by": "api",
            "call_id": "CA123",
            "trigger_uuid": "trigger-uuid-123",
        },
    )


def test_workflow_uuid_test_route_uses_draft_and_template_context():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _active_workflow()
    draft = SimpleNamespace(
        id=88,
        workflow_json={"nodes": [], "edges": []},
        template_context_variables={"name": "john", "age": 12, "rank": 2},
    )
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.public_agent.db_client") as mock_db,
        patch("api.routes.public_agent.call_concurrency") as mock_concurrency,
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
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()

        mock_db.validate_api_key = AsyncMock(
            return_value=SimpleNamespace(id=7, organization_id=11, created_by=22)
        )
        mock_db.get_workflow_by_uuid = AsyncMock(return_value=workflow)
        mock_db.get_draft_version = AsyncMock(return_value=draft)
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=501))
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            f"/public/agent/test/workflow/{workflow.workflow_uuid}",
            headers={"X-API-Key": "test-api-key"},
            json={
                "phone_number": "+15551234567",
                "initial_context": {"name": "tom"},
            },
        )

    assert response.status_code == 200
    assert mock_db.get_draft_version.await_count == 2
    create_kwargs = mock_db.create_workflow_run.await_args.kwargs
    assert create_kwargs["definition_id"] == draft.id
    assert create_kwargs["initial_context"]["name"] == "tom"
    assert create_kwargs["initial_context"]["age"] == 12
    assert create_kwargs["initial_context"]["rank"] == 2
    assert create_kwargs["initial_context"]["trigger_mode"] == "test"
    mock_db.update_workflow_run.assert_awaited_once_with(
        run_id=501,
        gathered_context={
            "provider": "twilio",
            "triggered_by": "api",
            "call_id": "CA123",
        },
    )


def test_trigger_route_still_returns_success_when_metadata_persistence_fails():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _active_workflow(trigger_path="trigger-uuid-123")
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.public_agent.db_client") as mock_db,
        patch("api.routes.public_agent.call_concurrency") as mock_concurrency,
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
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()

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
        mock_db.update_workflow_run = AsyncMock(side_effect=Exception("db down"))

        response = client.post(
            "/public/agent/trigger-uuid-123",
            headers={"X-API-Key": "test-api-key"},
            json={"phone_number": "+15551234567"},
        )

    assert response.status_code == 200
    provider.initiate_call.assert_awaited_once()
    mock_db.update_workflow_run.assert_awaited_once()
    mock_concurrency.release_workflow_run_slot.assert_not_awaited()


def test_trigger_route_rejects_when_concurrency_limit_reached():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _active_workflow(trigger_path="trigger-uuid-123")
    provider = _provider()

    with (
        patch("api.routes.public_agent.db_client") as mock_db,
        patch("api.routes.public_agent.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.public_agent.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(
            side_effect=CallConcurrencyLimitError(
                organization_id=11,
                source="public_agent",
                wait_time=0,
                max_concurrent=2,
            )
        )
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
        mock_db.create_workflow_run = AsyncMock()

        response = client.post(
            "/public/agent/trigger-uuid-123",
            headers={"X-API-Key": "test-api-key"},
            json={"phone_number": "+15551234567"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Concurrent call limit reached"
    mock_db.create_workflow_run.assert_not_called()


def test_trigger_route_releases_concurrency_slot_when_quota_fails():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _active_workflow(trigger_path="trigger-uuid-123")
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=False, error_message="Quota exceeded")
    )

    with (
        patch("api.routes.public_agent.db_client") as mock_db,
        patch("api.routes.public_agent.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.public_agent.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.public_agent.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=object())
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()

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

    assert response.status_code == 402
    mock_concurrency.release_workflow_run_slot.assert_awaited_once_with(501)
    provider.initiate_call.assert_not_awaited()


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
