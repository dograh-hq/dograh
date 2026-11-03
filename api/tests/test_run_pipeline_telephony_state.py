"""The telephony pipeline entry point is where a run becomes 'running'.

The media socket can open while the callee is still ringing, so the run is
claimed only once the stream has started and the pipeline is being set up.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.pipecat import run_pipeline as run_pipeline_module


class _ReachedSetup(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed", [True, False])
async def test_stream_start_claims_run_before_pipeline_setup(monkeypatch, claimed):
    """A lost claim still sets the pipeline up. The run is already terminal only
    when an answered call's hangup callback won the race, and the pipeline's own
    finalization is what records that call's disposition and fires its webhooks.
    """
    workflow = SimpleNamespace(
        organization_id=7, workflow_configurations=None, user_id=99
    )
    workflow_run = SimpleNamespace(
        workflow_id=1,
        initial_context={},
        definition=SimpleNamespace(workflow_configurations={}),
    )
    mark_running = AsyncMock(return_value=claimed)
    monkeypatch.setattr(
        run_pipeline_module.db_client, "get_workflow", AsyncMock(return_value=workflow)
    )
    monkeypatch.setattr(
        run_pipeline_module.db_client,
        "get_workflow_run",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(
        run_pipeline_module.db_client, "mark_workflow_run_running", mark_running
    )
    monkeypatch.setattr(
        "api.services.configuration.run_model_configuration."
        "get_effective_ai_model_configuration_for_run",
        AsyncMock(side_effect=_ReachedSetup),
    )
    monkeypatch.setattr(
        run_pipeline_module.call_concurrency, "unregister_active_call", AsyncMock()
    )

    with pytest.raises(_ReachedSetup):
        await run_pipeline_module.run_pipeline_telephony(
            websocket=object(),
            provider_name="telnyx",
            workflow_id=1,
            workflow_run_id=44,
            organization_id=7,
            call_id="call-1",
            transport_kwargs={},
        )

    mark_running.assert_awaited_once_with(44, 7)
