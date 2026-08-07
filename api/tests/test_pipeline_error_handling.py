from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import ErrorFrame

from api.services.pipecat.event_handlers import register_event_handlers


class _EventSource:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def decorator(handler):
            self.handlers[name] = handler
            return handler

        return decorator


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
        audio_config=SimpleNamespace(pipeline_sample_rate=16000),
    )

    await task.handlers["on_pipeline_error"](
        task,
        ErrorFrame("unrecoverable failure", fatal=True),
    )

    engine.end_call_with_reason.assert_awaited_once()
