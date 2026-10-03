import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import EndFrame, ErrorFrame
from pipecat.pipeline.worker import ProcessorUnusablePolicy
from pipecat.utils.enums import EndTaskReason
from pipecat.utils.errors import ErrorCategory

from api.enums import WorkflowRunState
from api.schemas.call_events import CallEventsSettings
from api.services.observability.call_events.runtime import CallEventsSession
from api.services.pipecat import event_handlers, pipeline_builder
from api.services.pipecat.event_handlers import register_event_handlers
from api.services.pipecat.termination_funnel_processor import (
    TerminationFunnelProcessor,
)
from api.services.workflow.pipecat_engine import PipecatEngine


class _EventSource:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def decorator(handler):
            self.handlers[name] = handler
            return handler

        return decorator


@pytest.fixture
def campaign_call(monkeypatch):
    """Wire the production error and completion handlers with external I/O mocked."""
    task = _EventSource()
    task.wait_for_observers = AsyncMock()
    task.turn_trace_observer = None
    context = {}

    async def end_call(reason, **kwargs):
        context.setdefault("call_status", reason)

    engine = SimpleNamespace(
        _active_agent=SimpleNamespace(visit_id="active", error=None),
        end_call_with_reason=AsyncMock(side_effect=end_call),
        get_gathered_context=AsyncMock(return_value=context),
        record_call_tags=Mock(),
        cleanup=AsyncMock(),
    )
    monkeypatch.setattr(
        event_handlers.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=SimpleNamespace(campaign_id=42, workflow_id=1)),
    )
    monkeypatch.setattr(event_handlers.db_client, "update_workflow_run", AsyncMock())
    for name in (
        "_capture_call_event",
        "notify_campaign_call_completed",
        "upload_workflow_run_artifacts",
        "enqueue_job",
    ):
        monkeypatch.setattr(event_handlers, name, AsyncMock())
    breaker = AsyncMock()
    monkeypatch.setattr(event_handlers.circuit_breaker, "record_and_evaluate", breaker)
    funnel = TerminationFunnelProcessor()
    transcript_log_coordinator = SimpleNamespace(flush=AsyncMock())
    call_events_session = SimpleNamespace(finish=AsyncMock())
    register_event_handlers(
        task=task,
        transport=_EventSource(),
        workflow_run_id=88,
        engine=engine,
        audio_buffer=SimpleNamespace(stop_recording=AsyncMock()),
        in_memory_logs_buffer=SimpleNamespace(
            contains_user_speech=lambda: False,
            is_empty=True,
            generate_transcript_text=lambda **kwargs: "",
        ),
        transcript_log_coordinator=transcript_log_coordinator,
        call_events_session=call_events_session,
        pipeline_metrics_aggregator=SimpleNamespace(
            get_all_usage_metrics_serialized=dict
        ),
        termination_funnel=funnel,
        audio_config=SimpleNamespace(pipeline_sample_rate=16000),
    )
    return SimpleNamespace(
        task=task,
        engine=engine,
        breaker=breaker,
        funnel=funnel,
        transcript_log_coordinator=transcript_log_coordinator,
        call_events_session=call_events_session,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("error_origin", ["agent", "funnel", "input_transport"])
async def test_terminal_errors_count_once_per_campaign_call(
    campaign_call, error_origin
):
    call = campaign_call
    error = ErrorFrame(
        "MiniMax TTS error: 1008 insufficient balance",
        category=ErrorCategory.QUOTA,
    )
    error.processor = SimpleNamespace(is_usable=False)

    for _ in range(2):
        if error_origin == "agent":
            await PipecatEngine.handle_agent_error(
                call.engine, call.engine._active_agent, error
            )
        elif error_origin == "funnel":
            await call.funnel._handler(EndTaskReason.PIPELINE_ERROR.value, error)
        else:
            await call.task.handlers["on_pipeline_error"](call.task, error)

    assert call.engine.end_call_with_reason.await_count == 2
    call.breaker.assert_not_awaited()
    await call.task.handlers["on_pipeline_finished"](
        call.task, EndFrame(reason=EndTaskReason.PIPELINE_ERROR.value)
    )

    call.breaker.assert_awaited_once_with(
        campaign_id=42,
        is_failure=True,
        workflow_run_id=88,
        reason="pipeline_error",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage", ["observers", "transcript", "workflow_lookup"]
)
async def test_finalization_error_cannot_suppress_campaign_failure(
    campaign_call, failure_stage
):
    call = campaign_call
    await call.engine.end_call_with_reason(EndTaskReason.PIPELINE_ERROR.value)
    failure = RuntimeError(f"{failure_stage} failed")
    if failure_stage == "observers":
        call.task.wait_for_observers.side_effect = failure
    elif failure_stage == "transcript":
        call.transcript_log_coordinator.flush.side_effect = failure
    else:
        # The finalizer's workflow lookup follows observer draining. Fail that
        # read without breaking the accounting path's own metadata lookup.
        async def fail_later_lookup():
            event_handlers.db_client.get_workflow_run_by_id.side_effect = failure

        call.task.wait_for_observers.side_effect = fail_later_lookup

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        await call.task.handlers["on_pipeline_finished"](call.task, EndFrame())

    call.breaker.assert_awaited_once_with(
        campaign_id=42,
        is_failure=True,
        workflow_run_id=88,
        reason="pipeline_error",
    )


@pytest.mark.asyncio
async def test_failure_accounting_error_does_not_abort_finalization(campaign_call):
    call = campaign_call
    await call.engine.end_call_with_reason(EndTaskReason.PIPELINE_ERROR.value)
    call.breaker.side_effect = RuntimeError("circuit breaker unavailable")

    await call.task.handlers["on_pipeline_finished"](call.task, EndFrame())

    call.breaker.assert_awaited_once()
    call.task.wait_for_observers.assert_awaited_once()
    call.transcript_log_coordinator.flush.assert_awaited_once()
    call.engine.cleanup.assert_awaited_once()
    event_handlers.notify_campaign_call_completed.assert_awaited_once_with(42, 88)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["finish", "recorder_cleanup"])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_diagnostics_failure_cannot_abort_call_completion(
    campaign_call, failure_stage, error_type
):
    call = campaign_call
    if failure_stage == "recorder_cleanup":
        # Exercise the real session: cleanup fails in _finish and, for ordinary
        # exceptions, fails again in finish's recovery branch.
        session = CallEventsSession(
            settings=CallEventsSettings(enabled=True, sink_type="bigquery"),
            organization_id=7,
            run_id=88,
            workflow_id=1,
            engine=call.engine,
        )
        session.recorder.cleanup = AsyncMock(side_effect=error_type("cleanup failed"))
        call.call_events_session.finish.side_effect = session.finish
    else:
        call.call_events_session.finish.side_effect = error_type("finalization failed")

    await call.task.handlers["on_pipeline_finished"](call.task, EndFrame())

    call.call_events_session.finish.assert_awaited_once()
    call.engine.cleanup.assert_awaited_once()
    completed_updates = [
        invocation.kwargs
        for invocation in event_handlers.db_client.update_workflow_run.await_args_list
        if invocation.kwargs.get("is_completed")
    ]
    assert len(completed_updates) == 1
    assert completed_updates[0]["run_id"] == 88
    assert completed_updates[0]["state"] == WorkflowRunState.COMPLETED.value
    event_handlers.notify_campaign_call_completed.assert_awaited_once_with(42, 88)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_kind", ["recoverable", "inactive_agent"])
async def test_errors_the_call_survives_do_not_count_as_campaign_failures(
    campaign_call, error_kind
):
    call = campaign_call
    error = ErrorFrame("MiniMax TTS quota", category=ErrorCategory.QUOTA)
    error.processor = SimpleNamespace(is_usable=error_kind == "recoverable")
    agent = (
        call.engine._active_agent
        if error_kind == "recoverable"
        else SimpleNamespace(visit_id="pending", error=None)
    )

    await PipecatEngine.handle_agent_error(call.engine, agent, error)
    call.engine.end_call_with_reason.assert_not_awaited()
    await call.engine.end_call_with_reason(EndTaskReason.USER_HANGUP.value)
    await call.task.handlers["on_pipeline_finished"](call.task, EndFrame())

    call.breaker.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_failure_outside_campaign_does_not_count(
    campaign_call, monkeypatch
):
    call = campaign_call
    monkeypatch.setattr(
        event_handlers.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=SimpleNamespace(campaign_id=None, workflow_id=1)),
    )
    await call.engine.end_call_with_reason(EndTaskReason.PIPELINE_ERROR.value)
    await call.task.handlers["on_pipeline_finished"](call.task, EndFrame())

    call.breaker.assert_not_awaited()


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
async def test_an_error_that_leaves_its_service_unusable_ends_call(monkeypatch):
    """The input transport's errors never reach the funnel, so this handler
    has to recognise the same verdict the funnel does."""
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

    error = ErrorFrame("STT service quota exceeded")
    error.processor = SimpleNamespace(is_usable=False)

    await task.handlers["on_pipeline_error"](task, error)

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
