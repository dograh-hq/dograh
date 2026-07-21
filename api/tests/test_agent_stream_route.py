from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.call_concurrency import CallConcurrencyLimitError


class _FakeWebSocket:
    def __init__(self, query_params: dict[str, str] | None = None):
        self.query_params = query_params or {}
        self.accept = AsyncMock()
        self.close = AsyncMock()


@pytest.mark.asyncio
async def test_agent_stream_uses_provider_path_param_not_query_param():
    from api.routes.agent_stream import agent_stream_websocket

    websocket = _FakeWebSocket(
        {
            "provider": "twilio",
            "custom": "value",
        }
    )
    workflow = SimpleNamespace(
        id=11,
        user_id=22,
        organization_id=33,
        template_context_variables={"existing": "context"},
        released_definition=SimpleNamespace(id=55, template_context_variables={}),
        current_definition=None,
    )
    workflow_run = SimpleNamespace(id=44)
    provider = SimpleNamespace(handle_external_websocket=AsyncMock())
    spec = SimpleNamespace(provider_cls=lambda _config: provider)

    with (
        patch("api.routes.agent_stream.telephony_registry") as registry,
        patch("api.routes.agent_stream.db_client") as db_client,
        patch("api.routes.agent_stream.call_concurrency") as mock_concurrency,
        patch(
            "api.routes.agent_stream.authorize_workflow_run_start",
            new=AsyncMock(
                return_value=SimpleNamespace(has_quota=True, error_message=None)
            ),
        ),
    ):
        slot = object()
        mock_concurrency.acquire_org_slot = AsyncMock(return_value=slot)
        mock_concurrency.bind_workflow_run = AsyncMock()
        mock_concurrency.release_slot = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()
        mock_concurrency.unregister_active_call = AsyncMock()

        registry.get_optional.return_value = spec
        db_client.get_workflow_by_uuid_unscoped = AsyncMock(return_value=workflow)
        db_client.create_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.update_workflow_run = AsyncMock()

        await agent_stream_websocket(websocket, "cloudonix", "agent-uuid")

    registry.get_optional.assert_called_once_with("cloudonix")
    db_client.create_workflow_run.assert_awaited_once()
    mock_concurrency.acquire_org_slot.assert_awaited_once_with(
        workflow.organization_id,
        source="agent_stream:cloudonix",
        timeout=0,
    )
    mock_concurrency.bind_workflow_run.assert_awaited_once_with(slot, workflow_run.id)
    mock_concurrency.unregister_active_call.assert_awaited_once_with(workflow_run.id)
    create_args = db_client.create_workflow_run.await_args.args
    create_kwargs = db_client.create_workflow_run.await_args.kwargs
    assert create_args[2] == "cloudonix"
    assert create_kwargs["organization_id"] == workflow.organization_id
    assert create_kwargs["initial_context"] == {
        "provider": "cloudonix",
        "direction": "inbound",
    }
    assert create_kwargs["definition_id"] == 55
    provider.handle_external_websocket.assert_awaited_once()
    _, provider_kwargs = provider.handle_external_websocket.await_args
    assert provider_kwargs["params"] == {"custom": "value"}
    websocket.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_stream_rejects_when_concurrency_limit_reached():
    from api.routes.agent_stream import agent_stream_websocket

    websocket = _FakeWebSocket()
    workflow = SimpleNamespace(
        id=11,
        user_id=22,
        organization_id=33,
        template_context_variables={},
        released_definition=SimpleNamespace(id=55, template_context_variables={}),
        current_definition=None,
    )
    spec = SimpleNamespace(provider_cls=lambda _config: object())

    with (
        patch("api.routes.agent_stream.telephony_registry") as registry,
        patch("api.routes.agent_stream.db_client") as db_client,
        patch("api.routes.agent_stream.call_concurrency") as mock_concurrency,
    ):
        registry.get_optional.return_value = spec
        db_client.get_workflow_by_uuid_unscoped = AsyncMock(return_value=workflow)
        db_client.create_workflow_run = AsyncMock()
        mock_concurrency.acquire_org_slot = AsyncMock(
            side_effect=CallConcurrencyLimitError(
                organization_id=workflow.organization_id,
                source="agent_stream:cloudonix",
                wait_time=0,
                max_concurrent=1,
            )
        )

        await agent_stream_websocket(websocket, "cloudonix", "agent-uuid")

    websocket.close.assert_awaited_once_with(
        code=1008,
        reason="Concurrent call limit reached",
    )
    db_client.create_workflow_run.assert_not_awaited()
