"""The transport entry points read ambient noise from the run's pinned definition.

``save_workflow_draft`` mirrors the draft into ``WorkflowModel.workflow_configurations``,
so reading that column would let an unpublished draft change the ambient noise of
a live call. Both entry points must hand the transport the configuration pinned on
``workflow_run.definition`` instead. The DB layer is mocked so this runs without
Postgres; the end-to-end counterpart for the pipeline-level knobs lives in
``tests/integrations/test_run_pipeline.py``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.pipecat import run_pipeline as run_pipeline_module

DRAFT_AMBIENT = {"enabled": True, "storage_key": "ambient-noise/1/1/draft.wav"}
PINNED_AMBIENT = {"enabled": True, "storage_key": "ambient-noise/1/1/published.wav"}


def _stub_db(monkeypatch):
    workflow = SimpleNamespace(
        id=1,
        organization_id=5,
        user_id=7,
        workflow_configurations={"ambient_noise_configuration": DRAFT_AMBIENT},
    )
    workflow_run = SimpleNamespace(
        id=42,
        workflow_id=1,
        initial_context=None,
        definition=SimpleNamespace(
            workflow_configurations={"ambient_noise_configuration": PINNED_AMBIENT}
        ),
    )
    monkeypatch.setattr(
        run_pipeline_module.db_client, "get_workflow", AsyncMock(return_value=workflow)
    )
    monkeypatch.setattr(
        run_pipeline_module.db_client,
        "get_workflow_run",
        AsyncMock(return_value=workflow_run),
    )


def _patch_effective_config():
    return patch(
        "api.services.configuration.ai_model_configuration."
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=SimpleNamespace(is_realtime=False, realtime=None)),
    )


@pytest.mark.asyncio
async def test_webrtc_transport_gets_pinned_ambient_noise(monkeypatch):
    _stub_db(monkeypatch)
    create_transport = AsyncMock(return_value=object())
    monkeypatch.setattr(
        run_pipeline_module, "create_webrtc_transport", create_transport
    )
    monkeypatch.setattr(run_pipeline_module, "_run_pipeline_impl", AsyncMock())

    with _patch_effective_config():
        await run_pipeline_module._run_pipeline_smallwebrtc_impl(
            webrtc_connection=object(),
            workflow_id=1,
            workflow_run_id=42,
            user_id=7,
        )

    create_transport.assert_awaited_once()
    assert create_transport.await_args.args[3] == PINNED_AMBIENT


@pytest.mark.asyncio
async def test_telephony_transport_gets_pinned_ambient_noise(monkeypatch):
    _stub_db(monkeypatch)
    transport_factory = AsyncMock(return_value=object())
    monkeypatch.setattr(
        run_pipeline_module.telephony_registry,
        "get",
        lambda name: SimpleNamespace(transport_factory=transport_factory),
    )
    monkeypatch.setattr(
        run_pipeline_module, "create_audio_config", lambda provider: object()
    )
    monkeypatch.setattr(run_pipeline_module, "_run_pipeline_impl", AsyncMock())

    with _patch_effective_config():
        await run_pipeline_module._run_pipeline_telephony_impl(
            websocket=object(),
            provider_name="twilio",
            workflow_id=1,
            workflow_run_id=42,
            organization_id=5,
            call_id="CA123",
            transport_kwargs={},
        )

    transport_factory.assert_awaited_once()
    assert transport_factory.await_args.kwargs["ambient_noise_config"] == PINNED_AMBIENT
