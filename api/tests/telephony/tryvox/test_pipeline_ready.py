from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.pipecat.run_pipeline import _run_pipeline_telephony_impl


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_error", [None, RuntimeError("cleanup failed")])
async def test_rejected_readiness_cleans_up_transport_without_starting_pipeline(
    cleanup_error,
):
    websocket = AsyncMock()
    transport = AsyncMock()
    transport.cleanup.side_effect = cleanup_error
    workflow = SimpleNamespace(
        id=7,
        organization_id=11,
        user_id=17,
        workflow_configurations={},
    )
    workflow_run = SimpleNamespace(
        workflow_id=7,
        initial_context={},
        definition=SimpleNamespace(workflow_configurations={}),
    )
    user_config = SimpleNamespace(is_realtime=False, realtime=None)
    transport_factory = AsyncMock(return_value=transport)
    on_ready = AsyncMock(return_value=False)

    with (
        patch("api.services.pipecat.run_pipeline.db_client") as db_client,
        patch(
            "api.services.pipecat.run_pipeline.telephony_registry.get",
            return_value=SimpleNamespace(transport_factory=transport_factory),
        ),
        patch("api.services.pipecat.run_pipeline.create_audio_config"),
        patch(
            "api.services.configuration.ai_model_configuration."
            "get_effective_ai_model_configuration_for_workflow",
            new_callable=AsyncMock,
            return_value=user_config,
        ),
        patch(
            "api.services.pipecat.run_pipeline._run_pipeline_impl",
            new_callable=AsyncMock,
        ) as run_pipeline,
    ):
        db_client.get_workflow = AsyncMock(return_value=workflow)
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)

        await _run_pipeline_telephony_impl(
            websocket,
            provider_name="tryvox",
            workflow_id=7,
            workflow_run_id=13,
            organization_id=11,
            call_id="call-123",
            transport_kwargs={"call_id": "call-123"},
            on_ready=on_ready,
        )

    transport_factory.assert_awaited_once()
    on_ready.assert_awaited_once_with()
    transport.cleanup.assert_awaited_once_with()
    websocket.close.assert_awaited_once_with(
        code=4401,
        reason="Stream capability unavailable",
    )
    run_pipeline.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_started", [False, True])
async def test_startup_rollback_only_runs_before_media_worker(worker_started):
    websocket = AsyncMock()
    transport = AsyncMock()
    workflow = SimpleNamespace(
        id=7,
        organization_id=11,
        user_id=17,
        workflow_configurations={},
    )
    workflow_run = SimpleNamespace(
        workflow_id=7,
        initial_context={},
        definition=SimpleNamespace(workflow_configurations={}),
    )
    user_config = SimpleNamespace(is_realtime=False, realtime=None)
    on_ready = AsyncMock(return_value=True)
    on_startup_failure = AsyncMock()

    async def fail_pipeline(*args, **kwargs):
        if worker_started:
            kwargs["on_worker_started"]()
        raise RuntimeError("pipeline failed")

    with (
        patch("api.services.pipecat.run_pipeline.db_client") as db_client,
        patch(
            "api.services.pipecat.run_pipeline.telephony_registry.get",
            return_value=SimpleNamespace(
                transport_factory=AsyncMock(return_value=transport)
            ),
        ),
        patch("api.services.pipecat.run_pipeline.create_audio_config"),
        patch(
            "api.services.configuration.ai_model_configuration."
            "get_effective_ai_model_configuration_for_workflow",
            new_callable=AsyncMock,
            return_value=user_config,
        ),
        patch(
            "api.services.pipecat.run_pipeline._run_pipeline_impl",
            new_callable=AsyncMock,
            side_effect=fail_pipeline,
        ),
    ):
        db_client.get_workflow = AsyncMock(return_value=workflow)
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)

        with pytest.raises(RuntimeError, match="pipeline failed"):
            await _run_pipeline_telephony_impl(
                websocket,
                provider_name="tryvox",
                workflow_id=7,
                workflow_run_id=13,
                organization_id=11,
                call_id="call-123",
                transport_kwargs={"call_id": "call-123"},
                on_ready=on_ready,
                on_startup_failure=on_startup_failure,
            )

    if worker_started:
        on_startup_failure.assert_not_awaited()
    else:
        on_startup_failure.assert_awaited_once_with()
