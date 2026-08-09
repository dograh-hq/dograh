from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import InputAudioRawFrame, StartFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.run_pipeline import (
    _FirstInboundAudioObserver,
    _MediaStartedGate,
    _run_pipeline_telephony_impl,
)


def _frame_pushed(frame, *, source):
    return FramePushed(
        source=source,
        destination=SimpleNamespace(),
        frame=frame,
        direction=FrameDirection.DOWNSTREAM,
        timestamp=0,
    )


class _FakeTask:
    """Minimal double exposing the PipelineWorker surface _MediaStartedGate uses."""

    def __init__(self):
        self.observers = []
        self._handlers: dict[str, list] = {}

    def add_observer(self, observer):
        self.observers.append(observer)

    def event_handler(self, event_name):
        def decorator(handler):
            self._handlers.setdefault(event_name, []).append(handler)
            return handler

        return decorator

    async def fire_pipeline_started(self):
        for handler in self._handlers.get("on_pipeline_started", []):
            await handler()

    async def fire_audio(self, transport_input):
        for observer in self.observers:
            await observer.on_push_frame(
                _frame_pushed(_input_audio_frame(), source=transport_input)
            )


def _input_audio_frame():
    return InputAudioRawFrame(audio=b"\x00\x00", sample_rate=8000, num_channels=1)


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
@pytest.mark.parametrize("media_started", [False, True])
async def test_startup_rollback_only_runs_before_media_started(media_started):
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
        if media_started:
            # Simulates _FirstInboundAudioObserver firing on real inbound
            # audio -- from _run_pipeline_telephony_impl's perspective, the
            # only thing that matters is whether on_media_started fired
            # before the failure, not how it fired.
            kwargs["on_media_started"]()
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

    if media_started:
        on_startup_failure.assert_not_awaited()
    else:
        on_startup_failure.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_first_inbound_audio_observer_fires_on_transport_input_audio():
    transport_input = SimpleNamespace()
    fired = []

    observer = _FirstInboundAudioObserver(transport_input, lambda: fired.append(True))

    await observer.on_push_frame(
        _frame_pushed(_input_audio_frame(), source=transport_input)
    )

    assert fired == [True]


@pytest.mark.asyncio
async def test_first_inbound_audio_observer_fires_only_once():
    transport_input = SimpleNamespace()
    fired = []

    observer = _FirstInboundAudioObserver(transport_input, lambda: fired.append(True))

    for _ in range(3):
        await observer.on_push_frame(
            _frame_pushed(_input_audio_frame(), source=transport_input)
        )

    assert fired == [True]


@pytest.mark.asyncio
async def test_first_inbound_audio_observer_ignores_other_sources():
    transport_input = SimpleNamespace()
    other_processor = SimpleNamespace()
    fired = []

    observer = _FirstInboundAudioObserver(transport_input, lambda: fired.append(True))

    await observer.on_push_frame(
        _frame_pushed(_input_audio_frame(), source=other_processor)
    )

    assert fired == []


@pytest.mark.asyncio
async def test_first_inbound_audio_observer_ignores_non_audio_frames():
    transport_input = SimpleNamespace()
    fired = []

    observer = _FirstInboundAudioObserver(transport_input, lambda: fired.append(True))

    await observer.on_push_frame(_frame_pushed(StartFrame(), source=transport_input))

    assert fired == []


@pytest.mark.asyncio
async def test_media_started_gate_requires_both_signals():
    task = _FakeTask()
    transport_input = SimpleNamespace()
    fired = []

    _MediaStartedGate(task, transport_input, lambda: fired.append(True))

    await task.fire_audio(transport_input)
    assert fired == [], "audio alone must not be enough"

    await task.fire_pipeline_started()
    assert fired == [True]


@pytest.mark.asyncio
async def test_media_started_gate_fires_when_pipeline_starts_first():
    task = _FakeTask()
    transport_input = SimpleNamespace()
    fired = []

    _MediaStartedGate(task, transport_input, lambda: fired.append(True))

    await task.fire_pipeline_started()
    assert fired == [], "pipeline start alone must not be enough"

    await task.fire_audio(transport_input)
    assert fired == [True]


@pytest.mark.asyncio
async def test_media_started_gate_fires_only_once():
    task = _FakeTask()
    transport_input = SimpleNamespace()
    fired = []

    _MediaStartedGate(task, transport_input, lambda: fired.append(True))

    await task.fire_pipeline_started()
    await task.fire_audio(transport_input)
    await task.fire_audio(transport_input)
    await task.fire_pipeline_started()

    assert fired == [True]


@pytest.mark.asyncio
async def test_media_started_gate_ignores_audio_from_other_sources():
    task = _FakeTask()
    transport_input = SimpleNamespace()
    other_processor = SimpleNamespace()
    fired = []

    _MediaStartedGate(task, transport_input, lambda: fired.append(True))

    await task.fire_pipeline_started()
    await task.fire_audio(other_processor)

    assert fired == []
