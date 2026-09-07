from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import ErrorFrame
from pipecat.pipeline.worker import ProcessorUnusablePolicy
from pipecat.utils.enums import EndTaskReason

from api.services.pipecat import pipeline_builder
from api.services.pipecat.event_handlers import register_event_handlers
from api.services.pipecat.termination_funnel_processor import (
    TerminationFunnelProcessor,
)


class _EventSource:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def decorator(handler):
            self.handlers[name] = handler
            return handler

        return decorator


def test_dograh_workers_cancel_when_a_processor_becomes_permanently_unusable(
    monkeypatch,
):
    captured = {}
    worker = SimpleNamespace(turn_tracking_observer=None)

    def capture_worker(*args, **kwargs):
        captured.update(kwargs)
        return worker

    monkeypatch.setenv("ENABLE_TURN_LOGGING", "false")
    monkeypatch.setattr(pipeline_builder, "PipelineWorker", capture_worker)

    result = pipeline_builder.create_pipeline_task(object(), workflow_run_id=88)

    assert result is worker
    assert captured["processor_unusable_policy"] is ProcessorUnusablePolicy.CANCEL


@pytest.mark.asyncio
async def test_nonfatal_pipeline_error_does_not_end_call(monkeypatch):
    task = _EventSource()
    transport = _EventSource()
    engine = SimpleNamespace(end_call_with_reason=AsyncMock())
    audio_buffer = SimpleNamespace(
        start_recording=AsyncMock(),
        stop_recording=AsyncMock(),
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.db_client.get_workflow_run_by_id",
        AsyncMock(),
    )

    register_event_handlers(
        task=task,
        transport=transport,
        workflow_run_id=88,
        engine=engine,
        audio_buffer=audio_buffer,
        in_memory_logs_buffer=SimpleNamespace(),
        transcript_log_coordinator=SimpleNamespace(),
        pipeline_metrics_aggregator=SimpleNamespace(),
        termination_funnel=TerminationFunnelProcessor(),
        audio_config=SimpleNamespace(pipeline_sample_rate=16000),
    )

    await task.handlers["on_pipeline_error"](
        task,
        ErrorFrame("recoverable provider reconnect", fatal=False),
    )

    engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
async def test_fatal_pipeline_error_still_ends_call(monkeypatch):
    task = _EventSource()
    transport = _EventSource()
    engine = SimpleNamespace(end_call_with_reason=AsyncMock())
    audio_buffer = SimpleNamespace(
        start_recording=AsyncMock(),
        stop_recording=AsyncMock(),
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.db_client.get_workflow_run_by_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers._capture_call_event",
        AsyncMock(),
    )

    register_event_handlers(
        task=task,
        transport=transport,
        workflow_run_id=88,
        engine=engine,
        audio_buffer=audio_buffer,
        in_memory_logs_buffer=SimpleNamespace(),
        transcript_log_coordinator=SimpleNamespace(),
        pipeline_metrics_aggregator=SimpleNamespace(),
        termination_funnel=TerminationFunnelProcessor(),
        audio_config=SimpleNamespace(pipeline_sample_rate=16000),
    )

    await task.handlers["on_pipeline_error"](
        task,
        ErrorFrame("unrecoverable failure", fatal=True),
    )

    engine.end_call_with_reason.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_funnel_disposes_of_the_call_through_the_same_path(monkeypatch):
    """Errors and cancellations raised inside the pipeline share one teardown.

    The funnel intercepts them before they reach the worker, so whatever it is
    handed must record the run and end the call exactly as the worker's own
    handler used to.
    """
    task = _EventSource()
    transport = _EventSource()
    engine = SimpleNamespace(end_call_with_reason=AsyncMock())
    audio_buffer = SimpleNamespace(
        start_recording=AsyncMock(),
        stop_recording=AsyncMock(),
    )
    termination_funnel = TerminationFunnelProcessor()
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.db_client.get_workflow_run_by_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers._capture_call_event",
        AsyncMock(),
    )

    register_event_handlers(
        task=task,
        transport=transport,
        workflow_run_id=88,
        engine=engine,
        audio_buffer=audio_buffer,
        in_memory_logs_buffer=SimpleNamespace(),
        transcript_log_coordinator=SimpleNamespace(),
        pipeline_metrics_aggregator=SimpleNamespace(),
        termination_funnel=termination_funnel,
        audio_config=SimpleNamespace(pipeline_sample_rate=16000),
    )

    await termination_funnel._handler(EndTaskReason.USER_HANGUP.value, None)

    engine.end_call_with_reason.assert_awaited_once_with(
        EndTaskReason.USER_HANGUP.value, abort_immediately=True
    )
