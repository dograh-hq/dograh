from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.enums import WorkflowRunMode, WorkflowRunState
from api.errors.telephony_errors import TelephonyError
from api.routes.telephony import _handle_telephony_websocket, handle_inbound_run, router
from api.services.auth.depends import get_user
from api.services.call_concurrency import CallConcurrencyLimitError


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=7,
        selected_organization_id=11,
    )
    return app


def _workflow(*, workflow_id: int = 33, user_id: int = 99):
    return SimpleNamespace(
        id=workflow_id,
        user_id=user_id,
        organization_id=11,
        template_context_variables={"template_key": "template-value"},
        released_definition=SimpleNamespace(
            id=77,
            template_context_variables={"template_key": "template-value"},
        ),
        current_definition=None,
    )


def _provider():
    return SimpleNamespace(
        PROVIDER_NAME="twilio",
        WEBHOOK_ENDPOINT="twilio/voice",
        validate_config=Mock(return_value=True),
        initiate_call=AsyncMock(
            return_value=SimpleNamespace(
                caller_number="+15550001111",
                provider_metadata={"call_id": "call-123"},
            )
        ),
    )


def test_initiate_call_executes_as_workflow_owner_for_shared_org_workflow():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.telephony.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number=None)
        )
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_draft_version = AsyncMock(return_value=None)
        mock_db.create_workflow_run = AsyncMock(
            return_value=SimpleNamespace(
                id=501,
                name="WR-TEL-OUT-00000001",
                initial_context={"template_key": "template-value"},
            )
        )
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id, "phone_number": "+15551234567"},
        )

    assert response.status_code == 200
    quota_mock.assert_awaited_once_with(
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
        workflow_run_id=501,
        actor_user=ANY,
    )
    mock_db.get_workflow.assert_awaited_once_with(workflow.id, organization_id=11)

    create_call = mock_db.create_workflow_run.await_args
    create_args = create_call.args
    create_kwargs = create_call.kwargs
    assert create_args[1] == workflow.id
    assert create_kwargs["user_id"] == workflow.user_id
    assert create_kwargs["organization_id"] == workflow.organization_id
    assert create_kwargs["definition_id"] == 77
    assert create_kwargs["initial_context"]["template_key"] == "template-value"
    mock_concurrency.acquire_org_slot.assert_awaited_once_with(
        workflow.organization_id,
        source="telephony_outbound",
        timeout=0,
    )
    mock_concurrency.bind_workflow_run.assert_awaited_once_with(slot, 501)

    initiate_kwargs = provider.initiate_call.await_args.kwargs
    assert initiate_kwargs["workflow_id"] == workflow.id
    # The media websocket URL is keyed on the org, not the workflow owner.
    assert initiate_kwargs["organization_id"] == workflow.organization_id
    webhook_url = initiate_kwargs["webhook_url"]
    assert f"organization_id={workflow.organization_id}" in webhook_url
    # The answer URL carries no workflow owner: nothing downstream scopes on it.
    assert "user_id=" not in webhook_url
    mock_db.get_user_configurations.assert_not_called()


def test_initiate_call_uses_draft_template_context_with_provider_overrides():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    draft = SimpleNamespace(
        id=88,
        template_context_variables={
            "phone_number": "template-phone",
            "called_number": "template-called",
            "provider": "template-provider",
            "draft_only": "kept",
        },
    )
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.telephony.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number=None)
        )
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_draft_version = AsyncMock(return_value=draft)
        mock_db.create_workflow_run = AsyncMock(
            return_value=SimpleNamespace(
                id=501,
                name="WR-TEL-OUT-00000001",
                initial_context={},
            )
        )
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id, "phone_number": "+15551234567"},
        )

    assert response.status_code == 200
    mock_db.get_draft_version.assert_awaited_once_with(workflow.id)
    create_kwargs = mock_db.create_workflow_run.await_args.kwargs
    assert create_kwargs["definition_id"] == draft.id
    assert create_kwargs["initial_context"]["phone_number"] == "+15551234567"
    assert create_kwargs["initial_context"]["called_number"] == "+15551234567"
    assert create_kwargs["initial_context"]["provider"] == provider.PROVIDER_NAME
    assert create_kwargs["initial_context"]["draft_only"] == "kept"


def test_initiate_call_uses_organization_preference_phone_number():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "api.routes.telephony.get_backend_endpoints",
            new=AsyncMock(return_value=("https://api.example.com", "wss://ignored")),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=object())
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number="+15550000000")
        )
        mock_db.get_configuration = Mock(
            return_value=SimpleNamespace(value={"test_phone_number": "+15557654321"})
        )
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_draft_version = AsyncMock(return_value=None)
        mock_db.create_workflow_run = AsyncMock(
            return_value=SimpleNamespace(
                id=501,
                name="WR-TEL-OUT-00000001",
                initial_context={},
            )
        )
        mock_db.update_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id},
        )

    assert response.status_code == 200
    assert provider.initiate_call.await_args.kwargs["to_number"] == "+15557654321"
    mock_db.get_user_configurations.assert_not_called()


def test_initiate_call_rejects_existing_run_for_different_workflow():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()
    quota_mock = AsyncMock(
        return_value=SimpleNamespace(has_quota=True, error_message="")
    )

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.telephony.authorize_workflow_run_start",
            new=quota_mock,
        ),
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=object())
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        mock_db.get_user_configurations = AsyncMock(
            return_value=SimpleNamespace(test_phone_number=None)
        )
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_workflow_run = AsyncMock(
            return_value=SimpleNamespace(
                id=501,
                workflow_id=44,
                name="WR-TEL-OUT-00000044",
                initial_context={},
            )
        )

        response = client.post(
            "/telephony/initiate-call",
            json={
                "workflow_id": workflow.id,
                "workflow_run_id": 501,
                "phone_number": "+15551234567",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "workflow_run_workflow_mismatch"
    mock_db.get_workflow_run.assert_awaited_once_with(501, organization_id=11)
    mock_concurrency.release_slot.assert_awaited_once()
    assert not mock_db.create_workflow_run.called
    assert provider.initiate_call.await_count == 0


def test_initiate_call_rejects_when_concurrency_limit_reached():
    app = _make_test_app()
    client = TestClient(app)

    workflow = _workflow()
    provider = _provider()

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.telephony.get_default_telephony_provider",
            new=AsyncMock(return_value=provider),
        ),
    ):
        mock_concurrency.acquire_org_slot = AsyncMock(
            side_effect=CallConcurrencyLimitError(
                organization_id=workflow.organization_id,
                source="telephony_outbound",
                wait_time=0,
                max_concurrent=1,
            )
        )
        mock_db.get_default_telephony_configuration = AsyncMock(
            return_value=SimpleNamespace(id=55)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.create_workflow_run = AsyncMock()

        response = client.post(
            "/telephony/initiate-call",
            json={"workflow_id": workflow.id, "phone_number": "+15551234567"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Concurrent call limit reached"
    mock_db.create_workflow_run.assert_not_called()
    provider.initiate_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_inbound_run_rejects_when_concurrency_limit_reached():
    request = SimpleNamespace(headers={}, url="https://api.example.com/inbound/run")
    provider_class = SimpleNamespace(
        PROVIDER_NAME="twilio",
        generate_validation_error_response=Mock(return_value="limit-response"),
    )
    normalized_data = SimpleNamespace(
        provider="twilio",
        direction="inbound",
        to_number="+15551230000",
        from_number="+15557650000",
        to_country="US",
        from_country="US",
        account_id="acct-1",
        call_id="call-1",
        raw_data={},
    )
    config = SimpleNamespace(id=55, organization_id=11)
    phone_row = SimpleNamespace(id=77, inbound_workflow_id=33)
    workflow = SimpleNamespace(id=33, user_id=99)
    provider_instance = SimpleNamespace(
        verify_inbound_signature=AsyncMock(return_value=True)
    )

    with (
        patch(
            "api.routes.telephony.parse_webhook_request",
            new=AsyncMock(return_value=({}, "raw-body")),
        ),
        patch(
            "api.routes.telephony._detect_provider",
            new=AsyncMock(return_value=provider_class),
        ),
        patch(
            "api.routes.telephony.normalize_webhook_data",
            return_value=normalized_data,
        ),
        patch("api.routes.telephony.db_client") as mock_db,
        patch(
            "api.routes.telephony.get_telephony_provider_by_id",
            new=AsyncMock(return_value=provider_instance),
        ),
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
    ):
        mock_db.find_inbound_route_by_account = AsyncMock(
            return_value=(config, phone_row)
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.create_workflow_run = AsyncMock()
        mock_concurrency.acquire_org_slot = AsyncMock(
            side_effect=CallConcurrencyLimitError(
                organization_id=config.organization_id,
                source="inbound:twilio",
                wait_time=0,
                max_concurrent=1,
            )
        )

        response = await handle_inbound_run(request)

    assert response == "limit-response"
    provider_class.generate_validation_error_response.assert_called_once_with(
        TelephonyError.CONCURRENT_CALL_LIMIT
    )
    mock_db.create_workflow_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_smallwebrtc_run_reaching_telephony_websocket_closes_without_running():
    websocket = AsyncMock()
    workflow_run = SimpleNamespace(
        id=501,
        workflow_id=33,
        mode=WorkflowRunMode.SMALLWEBRTC.value,
        state=WorkflowRunState.INITIALIZED.value,
        initial_context={},
        gathered_context={},
    )
    workflow = SimpleNamespace(id=33, organization_id=11, user_id=99)
    provider_lookup = AsyncMock()

    with (
        patch("api.routes.telephony.db_client") as mock_db,
        patch("api.routes.telephony.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.telephony.get_telephony_provider_for_run",
            new=provider_lookup,
        ),
    ):
        mock_concurrency.unregister_active_call = AsyncMock()
        mock_db.get_workflow_run = AsyncMock(return_value=workflow_run)
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.update_workflow_run = AsyncMock()

        await _handle_telephony_websocket(websocket, 33, 11, 501)

    mock_db.get_workflow_run.assert_awaited_once_with(501, organization_id=11)
    mock_db.get_workflow.assert_awaited_once_with(33, organization_id=11)
    websocket.close.assert_awaited_once_with(
        code=4400,
        reason=(
            "smallwebrtc runs connect through the WebRTC signaling endpoint, "
            "not the telephony websocket"
        ),
    )
    assert mock_db.update_workflow_run.await_count == 0
    assert provider_lookup.await_count == 0
    mock_concurrency.unregister_active_call.assert_not_awaited()
