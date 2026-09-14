"""ENABLE_CALL_RECORDING_UPLOAD gates the end-of-call audio upload."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


async def _run_pipeline_finished(
    monkeypatch, *, recording_upload_enabled: bool, campaign_id: int | None = None
):
    """Drive on_pipeline_finished with audio in the buffers, returning the
    kwargs the artifact upload was called with."""
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.ENABLE_CALL_RECORDING_UPLOAD",
        recording_upload_enabled,
    )

    uploads: dict = {}

    async def fake_upload(workflow_run_id, **kwargs):
        uploads.update(kwargs)

    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.upload_workflow_run_artifacts",
        fake_upload,
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.db_client.get_workflow_run_by_id",
        AsyncMock(
            return_value=(
                SimpleNamespace(campaign_id=campaign_id, workflow_id=1)
                if campaign_id
                else None
            )
        ),
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.db_client.update_workflow_run",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers._capture_call_event",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.enqueue_job",
        AsyncMock(),
    )

    task = _EventSource()
    task.wait_for_observers = AsyncMock()
    task.turn_trace_observer = None
    transport = _EventSource()
    engine = SimpleNamespace(
        end_call_with_reason=AsyncMock(),
        record_call_tags=lambda tags: None,
        record_context=lambda ctx: None,
        get_gathered_context=AsyncMock(return_value={}),
        cleanup=AsyncMock(),
    )
    logs_buffer = SimpleNamespace(
        contains_user_speech=lambda: False,
        is_empty=True,
        generate_transcript_text=lambda include_end_timestamps=False: "hello",
    )

    buffers = register_event_handlers(
        task=task,
        transport=transport,
        workflow_run_id=88,
        engine=engine,
        audio_buffer=SimpleNamespace(
            start_recording=AsyncMock(),
            stop_recording=AsyncMock(),
        ),
        in_memory_logs_buffer=logs_buffer,
        transcript_log_coordinator=SimpleNamespace(flush=AsyncMock()),
        pipeline_metrics_aggregator=SimpleNamespace(
            get_all_usage_metrics_serialized=lambda: {}
        ),
        termination_funnel=TerminationFunnelProcessor(),
        audio_config=SimpleNamespace(pipeline_sample_rate=16000),
    )

    pcm = b"\x00\x01" * 160
    await buffers.mixed.append(pcm)
    await buffers.user.append(pcm)
    await buffers.bot.append(pcm)

    await task.handlers["on_pipeline_finished"](task, None)
    return uploads


@pytest.mark.asyncio
async def test_campaign_is_notified_after_pipeline_run_is_terminal(monkeypatch):
    from api.services.campaign import campaign_event_publisher
    from api.services.pipecat import event_handlers

    updates_at_notification = []

    async def publish(campaign_id, run_id):
        updates_at_notification.append(
            event_handlers.db_client.update_workflow_run.await_args.kwargs
        )

    publisher = SimpleNamespace(publish_call_completed=AsyncMock(side_effect=publish))
    monkeypatch.setattr(
        campaign_event_publisher,
        "get_campaign_event_publisher",
        AsyncMock(return_value=publisher),
    )
    uploads = await _run_pipeline_finished(
        monkeypatch, recording_upload_enabled=True, campaign_id=48
    )
    publisher.publish_call_completed.assert_awaited_once_with(48, 88)
    assert updates_at_notification[0]["state"] == "completed"
    assert updates_at_notification[0]["is_completed"] is True
    assert uploads["mixed_audio_wav"]


@pytest.mark.asyncio
async def test_recordings_are_uploaded_by_default(monkeypatch):
    uploads = await _run_pipeline_finished(monkeypatch, recording_upload_enabled=True)

    assert uploads["mixed_audio_wav"]
    assert uploads["user_audio_wav"]
    assert uploads["bot_audio_wav"]
    assert uploads["transcript_text"] == "hello"


@pytest.mark.asyncio
async def test_recordings_are_skipped_when_upload_disabled(monkeypatch):
    uploads = await _run_pipeline_finished(monkeypatch, recording_upload_enabled=False)

    assert uploads["mixed_audio_wav"] is None
    assert uploads["user_audio_wav"] is None
    assert uploads["bot_audio_wav"] is None
    # The transcript is a separate artifact and must still be uploaded.
    assert uploads["transcript_text"] == "hello"
