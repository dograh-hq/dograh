"""The call-start sequence: ring until the fetch settles, then build and open.

The same sequence runs for every transport and both pipeline shapes, so it is
exercised here against stubs rather than per transport.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.services.pipecat import event_handlers
from api.services.pipecat.event_handlers import register_event_handlers
from api.services.pipecat.pre_call_fetch import PreCallFetchResult
from api.services.pipecat.termination_funnel_processor import TerminationFunnelProcessor
from api.services.workflow.pipecat_engine import PipecatEngine

RUN_CONFIG = "authorized-configuration"


class EventSource:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def register(fn):
            self.handlers[name] = fn
            return fn

        return register


class Call:
    """One call's start sequence wired to stubs, with an ordered event log."""

    def __init__(self, monkeypatch, *, fetch_task, is_child=True):
        self.events: list[str] = []
        self.task, self.transport = EventSource(), EventSource()
        self.transport.output = Mock(
            return_value=SimpleNamespace(queue_frame=AsyncMock())
        )
        self.ring_started = asyncio.Event()
        self.engine = PipecatEngine(
            workflow=None, call_context_vars={"customer": "before"}
        )
        self.engine.active_agent.workflow = SimpleNamespace(
            start_node_id="start", nodes={}
        )
        self.engine.active_agent.is_child = is_child
        self.engine.set_node = AsyncMock()
        self.engine.queue_node_opening = AsyncMock()
        self.engine.call_monitor = Mock()
        self.engine.start_initial_agent = AsyncMock(return_value=True)
        self.engine.finalize_initial_agent = Mock(
            side_effect=lambda config: self.events.append(f"finalize:{config}")
        )
        self.engine.record_context = Mock()
        self.engine.is_call_disposed = Mock(return_value=False)
        self.engine.end_call_with_reason = AsyncMock()
        self.engine._audio_config = SimpleNamespace(transport_out_sample_rate=16000)

        async def ring(*, stop_event, sample_rate, queue_frame, **_kwargs):
            assert sample_rate == 16000
            self.events.append("ring:start")
            self.ring_started.set()
            await stop_event.wait()
            self.events.append("ring:stop")

        monkeypatch.setattr(
            "api.services.pipecat.audio_playback.play_hold_audio_loop", ring
        )
        self.apply_overrides = AsyncMock(return_value=None)
        monkeypatch.setattr(
            event_handlers, "apply_pre_call_model_overrides", self.apply_overrides
        )
        monkeypatch.setattr(event_handlers, "_capture_call_event", AsyncMock())
        self.persist = AsyncMock()
        monkeypatch.setattr(
            event_handlers.db_client, "update_workflow_run", self.persist
        )
        self.workflow_run = SimpleNamespace(id=1)
        register_event_handlers(
            task=self.task,
            transport=self.transport,
            workflow_run_id=1,
            engine=self.engine,
            audio_buffer=SimpleNamespace(
                start_recording=AsyncMock(), stop_recording=AsyncMock()
            ),
            in_memory_logs_buffer=SimpleNamespace(),
            transcript_log_coordinator=SimpleNamespace(),
            pipeline_metrics_aggregator=SimpleNamespace(),
            termination_funnel=TerminationFunnelProcessor(),
            audio_config=SimpleNamespace(pipeline_sample_rate=16000),
            pre_call_fetch_task=fetch_task,
            workflow_run=self.workflow_run,
            organization_id=7,
            run_model_configuration=RUN_CONFIG,
        )

    async def ready(self) -> asyncio.Task:
        await self.task.handlers["on_pipeline_started"](self.task, None)
        return asyncio.create_task(
            self.transport.handlers["on_client_connected"](self.transport, None)
        )


def pending_fetch(result: PreCallFetchResult):
    release = asyncio.Event()

    async def fetch():
        await release.wait()
        return result

    return asyncio.create_task(fetch()), release


@pytest.mark.asyncio
async def test_rings_until_the_fetch_settles_then_builds_the_agent_from_it(
    monkeypatch,
):
    fetched = PreCallFetchResult(
        initial_context={"customer": "after"},
        model_overrides={"tts": {"settings": {"voice": "Alice"}}},
        outcome="completed",
    )
    fetch_task, release = pending_fetch(fetched)
    call = Call(monkeypatch, fetch_task=fetch_task)
    call.apply_overrides.return_value = "patched-configuration"
    connected = await call.ready()
    try:
        await asyncio.wait_for(call.ring_started.wait(), 1)
        call.engine.finalize_initial_agent.assert_not_called()
        call.engine.start_initial_agent.assert_not_awaited()
        release.set()
        await asyncio.wait_for(connected, 1)
    finally:
        connected.cancel()

    assert call.events == [
        "ring:start",
        "ring:stop",
        "finalize:patched-configuration",
    ]
    assert call.engine._call_context_vars["customer"] == "after"
    call.apply_overrides.assert_awaited_once_with(
        organization_id=7,
        workflow_run=call.workflow_run,
        run_model_configuration=RUN_CONFIG,
        fetched=fetched,
    )
    call.engine.record_context.assert_called_once_with(
        {"pre_call_fetch_outcome": "completed"}
    )
    call.persist.assert_awaited_once()
    assert call.persist.await_args.kwargs["initial_context"]["customer"] == "after"
    call.engine.start_initial_agent.assert_awaited_once()
    call.engine.set_node.assert_awaited_once_with("start")
    call.engine.queue_node_opening.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_overrides_keep_the_authorized_configuration(monkeypatch):
    fetch_task, release = pending_fetch(
        PreCallFetchResult(model_overrides={"stt": {}}, outcome="completed")
    )
    release.set()
    call = Call(monkeypatch, fetch_task=fetch_task)
    connected = await call.ready()
    await asyncio.wait_for(connected, 1)
    call.apply_overrides.assert_awaited_once()
    call.engine.finalize_initial_agent.assert_called_once_with(RUN_CONFIG)
    call.engine.start_initial_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_realtime_run_ignores_model_overrides(monkeypatch):
    fetch_task, release = pending_fetch(
        PreCallFetchResult(
            initial_context={"customer": "after"},
            model_overrides={"tts": {"settings": {"voice": "Alice"}}},
            outcome="completed",
        )
    )
    release.set()
    call = Call(monkeypatch, fetch_task=fetch_task, is_child=False)
    connected = await call.ready()
    await asyncio.wait_for(connected, 1)
    call.apply_overrides.assert_not_awaited()
    call.engine.finalize_initial_agent.assert_called_once_with(RUN_CONFIG)
    assert call.engine._call_context_vars["customer"] == "after"


@pytest.mark.asyncio
async def test_hangup_while_ringing_stops_the_ringer_and_never_starts_the_agent(
    monkeypatch,
):
    fetch_task, release = pending_fetch(PreCallFetchResult(outcome="completed"))
    call = Call(monkeypatch, fetch_task=fetch_task)
    connected = await call.ready()
    try:
        await asyncio.wait_for(call.ring_started.wait(), 1)
        call.engine.is_call_disposed.return_value = True
        release.set()
        await asyncio.wait_for(connected, 1)
    finally:
        connected.cancel()
    assert call.events == ["ring:start", "ring:stop"]
    call.engine.finalize_initial_agent.assert_not_called()
    call.engine.start_initial_agent.assert_not_awaited()
    call.persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_fetch_the_agent_starts_at_once(monkeypatch):
    call = Call(monkeypatch, fetch_task=None)
    connected = await call.ready()
    await asyncio.wait_for(connected, 1)
    assert call.events == [f"finalize:{RUN_CONFIG}"]
    call.apply_overrides.assert_not_awaited()
    call.engine.record_context.assert_not_called()
    call.engine.start_initial_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_fetch_that_already_settled_does_not_ring(monkeypatch):
    fetch_task, release = pending_fetch(
        PreCallFetchResult(initial_context={"customer": "after"}, outcome="completed")
    )
    release.set()
    await fetch_task
    call = Call(monkeypatch, fetch_task=fetch_task)
    connected = await call.ready()
    await asyncio.wait_for(connected, 1)
    assert call.events == [f"finalize:{RUN_CONFIG}"]
    assert call.engine._call_context_vars["customer"] == "after"


@pytest.mark.asyncio
async def test_recorded_opening_does_not_wait_for_generation(monkeypatch):
    call = Call(monkeypatch, fetch_task=None)
    started, release = asyncio.Event(), asyncio.Event()

    async def start():
        started.set()
        await release.wait()
        return True

    call.engine.start_initial_agent.side_effect = start
    call.engine.get_start_greeting = Mock(return_value=("audio", "42"))
    connected = await call.ready()
    try:
        await asyncio.wait_for(connected, 1)
        await asyncio.wait_for(started.wait(), 1)
        call.engine.queue_node_opening.assert_awaited_once()
        assert not call.engine.generation_ready
        call.engine.set_node.assert_not_awaited()
        release.set()
        assert await call.engine.wait_for_generation_ready()
        call.engine.set_node.assert_awaited_once()
    finally:
        await call.engine.cancel_initial_agent_startup()


@pytest.mark.asyncio
async def test_realtime_recording_handoff_waits_for_node_preparation(monkeypatch):
    call = Call(monkeypatch, fetch_task=None, is_child=False)
    prepared = asyncio.Event()

    async def set_node(_node_id):
        await prepared.wait()

    call.engine.set_node.side_effect = set_node
    handler = AsyncMock()
    call.engine.active_agent.llm = SimpleNamespace(handle_prerecorded_greeting=handler)
    call.engine.begin_initial_agent_startup()
    handoff = asyncio.create_task(
        call.engine._open_realtime_after_recorded_greeting("Welcome.")
    )
    try:
        await asyncio.sleep(0)
        handler.assert_not_awaited()
        prepared.set()
        await asyncio.wait_for(handoff, 1)
        handler.assert_awaited_once_with(call.engine.context, "Welcome.")
    finally:
        handoff.cancel()
        await asyncio.gather(handoff, return_exceptions=True)
        await call.engine.cancel_initial_agent_startup()


@pytest.mark.asyncio
async def test_cancelling_one_waiter_keeps_shared_startup_running(monkeypatch):
    call = Call(monkeypatch, fetch_task=None)
    release = asyncio.Event()
    call.engine.start_initial_agent.side_effect = release.wait
    call.engine.begin_initial_agent_startup()
    waiter = asyncio.create_task(call.engine.wait_for_generation_ready())
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert not call.engine._initial_agent_startup.done()
    release.set()
    assert await call.engine.wait_for_generation_ready()
    call.engine.start_initial_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_cancels_preparation_and_prevents_node_setup(monkeypatch):
    call = Call(monkeypatch, fetch_task=None)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def start():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    call.engine.start_initial_agent.side_effect = start
    call.engine.begin_initial_agent_startup()
    await asyncio.wait_for(entered.wait(), 1)
    await call.engine.cleanup()
    assert cancelled.is_set()
    assert not await call.engine.wait_for_generation_ready()
    call.engine.set_node.assert_not_awaited()
