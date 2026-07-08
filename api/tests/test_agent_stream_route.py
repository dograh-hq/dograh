from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


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
    )
    workflow_run = SimpleNamespace(id=44)
    provider = SimpleNamespace(handle_external_websocket=AsyncMock())
    spec = SimpleNamespace(provider_cls=lambda _config: provider)

    with (
        patch("api.routes.agent_stream.telephony_registry") as registry,
        patch("api.routes.agent_stream.db_client") as db_client,
        patch(
            "api.routes.agent_stream.authorize_workflow_run_start",
            new=AsyncMock(
                return_value=SimpleNamespace(has_quota=True, error_message=None)
            ),
        ),
    ):
        registry.get_optional.return_value = spec
        db_client.get_workflow_by_uuid_unscoped = AsyncMock(return_value=workflow)
        db_client.create_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.update_workflow_run = AsyncMock()

        await agent_stream_websocket(websocket, "cloudonix", "agent-uuid")

    registry.get_optional.assert_called_once_with("cloudonix")
    db_client.create_workflow_run.assert_awaited_once()
    create_args = db_client.create_workflow_run.await_args.args
    create_kwargs = db_client.create_workflow_run.await_args.kwargs
    assert create_args[2] == "cloudonix"
    assert create_kwargs["initial_context"] == {
        "existing": "context",
        "provider": "cloudonix",
        "direction": "inbound",
    }
    provider.handle_external_websocket.assert_awaited_once()
    _, provider_kwargs = provider.handle_external_websocket.await_args
    assert provider_kwargs["params"] == {"custom": "value"}
    websocket.close.assert_not_awaited()
