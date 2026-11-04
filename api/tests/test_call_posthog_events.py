from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.enums import PostHogEvent
from api.services.pipecat import event_handlers


@pytest.fixture
def capture(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(event_handlers, "capture_tenant_event", mock)
    return mock


@pytest.mark.asyncio
async def test_call_event_names_the_workflow_by_uuid_and_the_tenant(
    monkeypatch, capture
):
    run = SimpleNamespace(
        mode="webrtc",
        initial_context={"direction": "inbound"},
        workflow=SimpleNamespace(workflow_uuid="wf-uuid", organization_id=42),
    )
    monkeypatch.setattr(
        event_handlers.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=run),
    )

    await event_handlers._capture_call_event(
        913, "stack-user-1", PostHogEvent.CALL_FAILED, {"error_reason": "busy"}
    )

    capture.assert_awaited_once_with(
        distinct_id="stack-user-1",
        event=PostHogEvent.CALL_FAILED,
        organization_id=42,
        properties={
            "workflow_uuid": "wf-uuid",
            "call_type": "webrtc",
            "call_direction": "inbound",
            "error_reason": "busy",
        },
    )


@pytest.mark.asyncio
async def test_call_event_for_a_missing_run_has_no_tenant(monkeypatch, capture):
    monkeypatch.setattr(
        event_handlers.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=None),
    )

    await event_handlers._capture_call_event(
        913, "stack-user-1", PostHogEvent.CALL_STARTED
    )

    kwargs = capture.await_args.kwargs
    assert kwargs["organization_id"] is None
    assert kwargs["properties"] == {
        "workflow_uuid": None,
        "call_type": None,
        "call_direction": None,
    }
