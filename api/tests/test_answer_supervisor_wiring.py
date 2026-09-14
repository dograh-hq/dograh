import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.turns.user_mute import (
    FirstSpeechUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)

from api.services.pipecat.event_handlers import register_event_handlers
from api.services.pipecat.run_pipeline import _create_user_mute_strategies
from api.services.pipecat.termination_funnel_processor import TerminationFunnelProcessor
from api.services.workflow import answer_classification_service
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_callbacks import UserIdleHandler


@pytest.mark.parametrize(
    "enabled, expected",
    [
        (False, MuteUntilFirstBotCompleteUserMuteStrategy),
        (True, FirstSpeechUserMuteStrategy),
    ],
)
def test_answer_handling_listens_before_the_first_bot_speech(enabled, expected):
    engine = SimpleNamespace(should_mute_user=AsyncMock())
    supervisor = SimpleNamespace() if enabled else None
    assert isinstance(_create_user_mute_strategies(engine, supervisor)[0], expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_start_node_sets_up_context_without_sleeping(monkeypatch, enabled):
    engine = PipecatEngine(workflow=None, call_context_vars={}, workflow_run_id=1)
    engine.answer_supervisor = SimpleNamespace() if enabled else None
    engine._setup_llm_context = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("api.services.workflow.pipecat_engine.asyncio.sleep", sleep)
    node = SimpleNamespace(delayed_start=True, delayed_start_duration=2.5)
    await engine._handle_start_node(node)
    sleep.assert_not_awaited()
    engine._setup_llm_context.assert_awaited_once_with(node)


@pytest.mark.asyncio
async def test_already_dispatched_idle_event_cannot_prompt_main_llm_while_supervised():
    engine = PipecatEngine(workflow=None, call_context_vars={}, workflow_run_id=1)
    engine.answer_supervisor = SimpleNamespace(blocks_workflow=True)
    aggregator = SimpleNamespace(push_frame=AsyncMock())
    handler = UserIdleHandler(engine)
    await handler.handle_idle(aggregator)
    aggregator.push_frame.assert_not_awaited()
    engine.answer_supervisor.blocks_workflow = False
    await handler.handle_idle(aggregator)
    aggregator.push_frame.assert_awaited_once()
    assert handler._retry_count == 1


class EventSource:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def register(fn):
            self.handlers[name] = fn
            return fn

        return register


@pytest.mark.asyncio
async def test_readiness_arms_before_fetch_and_opens_only_after_permission(monkeypatch):
    task, transport = EventSource(), EventSource()
    transport.output = Mock(return_value=SimpleNamespace(queue_frame=AsyncMock()))
    fetched, permission = asyncio.Event(), asyncio.Event()
    supervisor = SimpleNamespace(arm=Mock())
    engine = SimpleNamespace(
        workflow=SimpleNamespace(start_node_id="start"),
        _call_context_vars={},
        set_node=AsyncMock(),
        queue_node_opening=AsyncMock(),
        handle_answer_supervision=AsyncMock(side_effect=permission.wait),
    )
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers._capture_call_event", AsyncMock()
    )

    async def fetch():
        await fetched.wait()
        return {}

    async def ring(*, stop_event, **kwargs):
        await stop_event.wait()

    monkeypatch.setattr("api.services.pipecat.event_handlers.play_audio_loop", ring)
    fetch_task = asyncio.create_task(fetch())
    register_event_handlers(
        task=task,
        transport=transport,
        workflow_run_id=1,
        engine=engine,
        audio_buffer=SimpleNamespace(
            start_recording=AsyncMock(), stop_recording=AsyncMock()
        ),
        in_memory_logs_buffer=SimpleNamespace(),
        transcript_log_coordinator=SimpleNamespace(),
        pipeline_metrics_aggregator=SimpleNamespace(),
        termination_funnel=TerminationFunnelProcessor(),
        audio_config=SimpleNamespace(pipeline_sample_rate=16000),
        pre_call_fetch_task=fetch_task,
        answer_supervisor=supervisor,
    )
    await task.handlers["on_pipeline_started"](task, None)
    supervisor.arm.assert_not_called()
    connected = asyncio.create_task(
        transport.handlers["on_client_connected"](transport, None)
    )
    try:
        async with asyncio.timeout(1):
            while not supervisor.arm.called:
                await asyncio.sleep(0)
        engine.set_node.assert_not_awaited()
        fetched.set()
        async with asyncio.timeout(1):
            while not engine.handle_answer_supervision.called:
                await asyncio.sleep(0)
        engine.set_node.assert_awaited_once_with("start")
        engine.queue_node_opening.assert_not_awaited()
        permission.set()
        await asyncio.wait_for(connected, 1)
        # Repeated readiness notifications cannot run the supervised action twice.
        await task.handlers["on_pipeline_started"](task, None)
        engine.handle_answer_supervision.assert_awaited_once()
    finally:
        connected.cancel()
        fetch_task.cancel()
        await asyncio.gather(connected, fetch_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_workflow_llm", [False, True])
async def test_saved_detector_settings_build_a_private_classifier_with_fixed_instructions(
    monkeypatch, use_workflow_llm
):
    from pipecat.processors.aggregators.llm_context import LLMContext

    from api.services.pipecat.run_pipeline import _create_answer_supervisor

    llm = SimpleNamespace(run_inference=AsyncMock(return_value="SCREENER"))
    workflow_factory = Mock(return_value=llm)
    provider_factory = Mock(return_value=llm)
    monkeypatch.setattr(
        "api.services.pipecat.run_pipeline.create_llm_service", workflow_factory
    )
    monkeypatch.setattr(
        "api.services.pipecat.run_pipeline.create_llm_service_from_provider",
        provider_factory,
    )
    context = LLMContext([{"role": "system", "content": "Workflow instructions"}])
    user_config = object()
    supervisor = _create_answer_supervisor(
        {
            "enabled": True,
            "use_workflow_llm": use_workflow_llm,
            "provider": "openai",
            "model": "gpt-4.1",
            "api_key": "test-key",
            "system_prompt": "Obsolete binary classifier prompt",
            "long_speech_timeout": 8,
        },
        call_direction="outbound",
        is_realtime=False,
        start_node=None,
        context=context,
        user_config=user_config,
        correlation_id="test-run",
    )
    try:
        assert supervisor.llm_gate().closed
        if use_workflow_llm:
            workflow_factory.assert_called_once_with(
                user_config,
                correlation_id="test-run",
                usage_context="voicemail_detection",
            )
            provider_factory.assert_not_called()
        else:
            provider_factory.assert_called_once_with(
                provider="openai",
                model="gpt-4.1",
                api_key="test-key",
                usage_context="voicemail_detection",
            )
            workflow_factory.assert_not_called()
        # A completed ambiguous machine turn reaches the private inference path.
        await supervisor._classify_turn("An ambiguous machine answer", 0)
        assert (await supervisor.wait_for_verdict()).action == "screen_then_rearm"
        inference = llm.run_inference.call_args
        assert inference.args[0] is not context
        assert (
            inference.kwargs["system_instruction"]
            == answer_classification_service._SYSTEM_PROMPT
        )
        assert (
            "Obsolete binary classifier prompt"
            not in inference.kwargs["system_instruction"]
        )
        assert context.messages == [
            {"role": "system", "content": "Workflow instructions"}
        ]
    finally:
        await supervisor.close()


@pytest.mark.parametrize(
    "direction, realtime, enabled",
    [("inbound", False, True), ("outbound", True, True), ("outbound", False, False)],
)
def test_unsupported_or_disabled_calls_do_not_create_a_classifier(
    monkeypatch, direction, realtime, enabled
):
    from pipecat.processors.aggregators.llm_context import LLMContext

    from api.services.pipecat.run_pipeline import _create_answer_supervisor

    factory = Mock()
    monkeypatch.setattr("api.services.pipecat.run_pipeline.create_llm_service", factory)
    monkeypatch.setattr(
        "api.services.pipecat.run_pipeline.create_llm_service_from_provider", factory
    )
    assert (
        _create_answer_supervisor(
            {"enabled": enabled},
            call_direction=direction,
            is_realtime=realtime,
            start_node=None,
            context=LLMContext(),
            user_config=None,
            correlation_id=None,
        )
        is None
    )
    factory.assert_not_called()
