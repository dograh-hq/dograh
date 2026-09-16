import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    InputAudioRawFrame,
    LLMMessagesAppendFrame,
    MetricsFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM, LLMService
from pipecat.services.openai.live import events
from pipecat.services.openai.live.llm import OpenAILiveLLMService
from pipecat.services.settings import LLMSettings
from pipecat.tests.mock_transport import MockTransport
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_mute import (
    CallbackUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)

from api.routes.user import get_default_configurations
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import (
    OpenAIRealtimeLLMConfiguration,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.realtime.openai_live import (
    VOICE_INSTRUCTIONS,
    DograhOpenAILiveLLMService,
)
from api.services.pipecat.realtime.openai_realtime import DograhOpenAIRealtimeLLMService
from api.services.pipecat.run_pipeline import _create_realtime_user_turn_config
from api.services.pipecat.service_factory import create_realtime_llm_service
from api.services.pipecat.worker_runner import run_pipeline_worker
from api.services.workflow.pipecat_engine import PipecatEngine


def make_service():
    service = DograhOpenAILiveLLMService(
        api_key="test-key", backend_model="gpt-5.4-mini"
    )
    service.send_client_event = AsyncMock()
    service.push_frame = AsyncMock()
    return service


def sent_events(service, event_type):
    return [
        call.args[0].to_payload()
        for call in service.send_client_event.await_args_list
        if call.args[0].type == event_type
    ]


async def start_session(service):
    await service._handle_evt_session_started(
        events.SessionStartedEvent(
            type="session.started",
            session=events.SessionResource(id="live_test", model="gpt-live-1"),
        )
    )


@pytest.mark.asyncio
async def test_model_dropdown_has_one_openai_provider_with_both_model_families():
    defaults = (await get_default_configurations()).model_dump()["realtime"]
    assert [key for key in defaults if key.startswith("openai")] == ["openai_realtime"]
    schema = defaults["openai_realtime"]
    assert schema["title"] == "OpenAI"
    assert {"gpt-live-1", "gpt-realtime-2.1"} <= set(
        schema["properties"]["model"]["examples"]
    )


@pytest.mark.parametrize(
    "model,service_type",
    [
        ("gpt-live-1", DograhOpenAILiveLLMService),
        ("gpt-realtime-2.1", DograhOpenAIRealtimeLLMService),
        ("gpt-realtime-2", DograhOpenAIRealtimeLLMService),
    ],
)
def test_saved_openai_provider_routes_by_model(model, service_type):
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_realtime",
                "api_key": "test-key",
                "model": model,
            },
        }
    )
    service = create_realtime_llm_service(config, SimpleNamespace())
    assert isinstance(service, service_type)
    if model == "gpt-live-1":
        assert config.realtime.voice == "marin"
        assert service._delegation.settings.model == "gpt-5.4-mini"
    else:
        assert config.realtime.voice == "alloy"


def test_live_turns_do_not_enable_local_interruptions():
    strategies, vad = _create_realtime_user_turn_config("openai_realtime", "gpt-live-1")
    assert vad is None
    assert strategies.start[0]._enable_interruptions is False
    assert strategies.stop[0].wait_for_transcript is False
    assert OpenAIRealtimeLLMConfiguration(api_key="test").model == "gpt-realtime-2"


@pytest.mark.asyncio
async def test_workflow_changes_update_backend_instructions_and_tools_without_restarting_voice():
    service = make_service()
    tool = FunctionSchema(
        name="lookup", description="Lookup", properties={}, required=[]
    )
    context = LLMContext(tools=ToolsSchema(standard_tools=[tool]))
    service._context = context
    await service._update_settings(
        LLMSettings(system_instruction="Ask for the account number.")
    )
    await service._handle_context(context)
    session = sent_events(service, "session.start")[0]["session"]
    assert session["instructions"] == VOICE_INSTRUCTIONS
    assert "account number" in session["delegation"]["responses"]["instructions"]
    assert session["delegation"]["responses"]["tools"][0]["name"] == "lookup"
    await start_session(service)
    context.set_tools(ToolsSchema(standard_tools=[]))
    await service._update_settings(
        LLMSettings(system_instruction="Ask for the address.")
    )
    update = sent_events(service, "session.update")[-1]["session"]
    assert "address" in update["delegation"]["responses"]["instructions"]
    assert update["delegation"]["responses"]["tools"] == []
    assert "instructions" not in update
    assert len(sent_events(service, "session.start")) == 1
    assert len(sent_events(service, "response.create")) == 1
    await service._handle_context(context)
    assert len(sent_events(service, "response.create")) == 1


@pytest.mark.asyncio
async def test_transition_skips_text_without_muting_and_updates_next_node(
    three_node_workflow_no_variable_extraction,
):
    service = make_service()
    context = LLMContext()
    task = SimpleNamespace(queue_frame=AsyncMock())
    workflow = three_node_workflow_no_variable_extraction
    engine = PipecatEngine(
        llm=service,
        context=context,
        task=task,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        is_realtime=True,
    )
    await engine.set_node("start")
    await service._handle_context(context)
    await start_session(service)
    service.send_client_event.reset_mock()

    transition = await engine._create_transition_func(
        "collect_info", "agent", transition_speech="Let me ask a few questions."
    )
    result_callback = AsyncMock()
    await transition(SimpleNamespace(arguments={}, result_callback=result_callback))

    task.queue_frame.assert_not_awaited()
    assert sent_events(service, "session.instructions.append") == []
    assert not await engine.should_mute_user(InputAudioRawFrame(bytes(480), 24000, 1))
    assert engine.active_agent.current_node.id == "agent"
    update = sent_events(service, "session.update")[-1]["session"]
    assert (
        workflow.nodes["agent"].prompt
        in update["delegation"]["responses"]["instructions"]
    )
    result_callback.assert_awaited_once()
    assert result_callback.await_args.args == ({"status": "done"},)


@pytest.mark.asyncio
async def test_static_greeting_waits_for_session_and_idle_prompts_stay_out_of_local_context():
    service = make_service()
    service._context = LLMContext()
    await service.process_frame(TTSSpeakFrame("Bonjour!"), FrameDirection.DOWNSTREAM)
    assert sent_events(service, "session.instructions.append") == []
    await start_session(service)
    instruction = sent_events(service, "session.instructions.append")[0]
    assert "Bonjour!" in instruction["content"]
    assert "without waiting" in instruction["content"]
    assert instruction["delegation_id"] is None
    assert sent_events(service, "session.commentary.append") == []
    assert sent_events(service, "response.create") == []
    await service.process_frame(
        LLMMessagesAppendFrame(
            [{"role": "user", "content": "Ask whether the caller is still there."}],
            run_llm=True,
        ),
        FrameDirection.DOWNSTREAM,
    )
    assert (
        "still there"
        in sent_events(service, "session.instructions.append")[-1]["content"]
    )
    assert service._context.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_rate", [8000, 16000, 24000])
async def test_mute_sends_silence_to_keep_live_running_and_unmute_restores_audio(
    sample_rate,
):
    service = make_service()
    service._session_started = True
    audio = InputAudioRawFrame(b"\x00\x01" * (sample_rate // 10), sample_rate, 1)
    # Prime the streaming resampler with speech before muting. Its buffered
    # samples must also be silenced when the mute takes effect.
    for _ in range(3):
        await service._send_user_audio(audio)
    service.send_client_event.reset_mock()
    await service.process_frame(UserMuteStartedFrame(), FrameDirection.DOWNSTREAM)
    for _ in range(10):
        await service._send_user_audio(audio)
    muted = b"".join(
        base64.b64decode(event["audio"])
        for event in sent_events(service, "session.input_audio.append")
    )
    assert 0.9 <= len(muted) / (24000 * 2) <= 1.1
    assert not any(muted)
    assert any(audio.audio)  # Keep the original frame intact for recording.
    service.send_client_event.reset_mock()
    await service.process_frame(UserMuteStoppedFrame(), FrameDirection.DOWNSTREAM)
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    for _ in range(3):
        await service._send_user_audio(audio)
    unmuted = b"".join(
        base64.b64decode(event["audio"])
        for event in sent_events(service, "session.input_audio.append")
    )
    assert any(unmuted)


@pytest.mark.asyncio
async def test_muted_startup_greeting_reaches_playback_and_releases_initial_mute():
    class ClockedLiveSocket:
        """Live cannot acknowledge instructions or speak without input audio."""

        def __init__(self):
            self.incoming = asyncio.Queue()
            self.instruction = None
            self.audio_before_greeting = None

        def __aiter__(self):
            return self

        async def __anext__(self):
            return json.dumps(await self.incoming.get())

        async def send(self, message):
            event = json.loads(message)
            if event["type"] == "session.start":
                await self.incoming.put(
                    {"type": "session.started", "session": {"id": "test"}}
                )
            elif event["type"] == "session.instructions.append":
                self.instruction = event
            elif event["type"] == "session.input_audio.append" and self.instruction:
                self.audio_before_greeting = base64.b64decode(event["audio"])
                await self.incoming.put(
                    {
                        "type": "session.instructions.appended",
                        "client_event_id": self.instruction["event_id"],
                    }
                )
                self.instruction = None
                # Audible greeting followed by silence. The real output
                # transport must derive bot-start/stop from these samples.
                pcm = b"\x00\x30" * 2400 + bytes(24000)
                await self.incoming.put(
                    {
                        "type": "session.output_audio.delta",
                        "delta": base64.b64encode(pcm).decode(),
                    }
                )
            elif event["type"] == "session.close":
                await self.incoming.put({"type": "session.closed"})

        async def close(self):
            pass

    socket = ClockedLiveSocket()
    service = DograhOpenAILiveLLMService(
        api_key="test-key", backend_model="gpt-5.4-mini"
    )
    context = LLMContext()
    service._context = context  # Dograh's engine initializes this before greeting.
    turns, _ = _create_realtime_user_turn_config("openai_realtime", "gpt-live-1")
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=turns,
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
        realtime_service_mode=False,
    )
    transport = MockTransport(
        TransportParams(audio_out_enabled=True), generate_audio=True
    )
    pipeline = Pipeline(
        [
            transport.input(),
            aggregators.user(),
            service,
            transport.output(),
            aggregators.assistant(),
        ]
    )
    with patch(
        "pipecat.services.openai.live.llm.websocket_connect",
        AsyncMock(return_value=socket),
    ):
        down, up = await asyncio.wait_for(
            run_test(
                pipeline, frames_to_send=[TTSSpeakFrame("Hello!"), SleepFrame(1.2)]
            ),
            5,
        )
    assert socket.audio_before_greeting
    assert not any(socket.audio_before_greeting)
    assert any(isinstance(frame, BotStartedSpeakingFrame) for frame in up)
    assert any(isinstance(frame, BotStoppedSpeakingFrame) for frame in up)
    assert any(isinstance(frame, UserMuteStoppedFrame) for frame in up)
    assert not service._user_is_muted
    assert not any(isinstance(frame, ErrorFrame) for frame in [*up, *down])


@pytest.mark.asyncio
async def test_only_workflow_control_waits_for_playback_and_disconnect_discards_pending_calls():
    service = make_service()
    service.register_function("transition", AsyncMock(), is_node_transition=True)
    service.register_function("lookup", AsyncMock())
    context = LLMContext()
    transition = FunctionCallFromLLM(
        function_name="transition", tool_call_id="call-1", arguments={}, context=context
    )
    lookup = FunctionCallFromLLM(
        function_name="lookup", tool_call_id="call-2", arguments={}, context=context
    )
    with patch.object(LLMService, "run_function_calls", new_callable=AsyncMock) as run:
        await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await service.run_function_calls([transition])
        run.assert_not_awaited()
        await service.run_function_calls([lookup])
        run.assert_awaited_once_with([lookup])
        await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        assert run.await_args_list[-1].args == ([transition],)
        await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await service.run_function_calls([transition])
        await service._disconnect()
        await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        assert run.await_count == 2


@pytest.mark.asyncio
async def test_live_seconds_are_deduplicated_and_backend_tokens_keep_their_model():
    service = make_service()
    service._setup = SimpleNamespace(enable_usage_metrics=True)
    for seconds in (2.0, 2.0, 1.0, 3.5):
        await service._report_usage(events.Usage(seconds=seconds))
    aggregator = PipelineMetricsAggregator()
    aggregator.push_frame = AsyncMock()
    for call in service.push_frame.await_args_list:
        await aggregator.process_frame(call.args[0], FrameDirection.DOWNSTREAM)
    usage = aggregator.get_all_usage_metrics_serialized()
    assert sum(usage["live_audio_seconds"].values()) == 3.5
    aggregator.reset_metrics()
    assert "live_audio_seconds" not in aggregator.get_all_usage_metrics_serialized()

    tokens = LLMUsageMetricsData(
        processor=service.name,
        model="gpt-live-1",
        value=LLMTokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    with patch.object(OpenAILiveLLMService, "push_frame", new_callable=AsyncMock):
        await DograhOpenAILiveLLMService.push_frame(
            service, MetricsFrame(data=[tokens])
        )
    assert tokens.model == "gpt-5.4-mini"
    assert "live" not in tokens.processor.lower()


@pytest.mark.asyncio
async def test_end_node_keeps_live_open_until_last_audio_reaches_caller(
    three_node_workflow_no_variable_extraction,
):
    class ClosingAnnouncementSocket:
        def __init__(self):
            self.incoming = asyncio.Queue()
            self.goodbye_requested = False
            self.audio_packets = 0
            self.closed_before_last_packet = None
            self.bot_was_speaking_at_close = None

        def __aiter__(self):
            return self

        async def __anext__(self):
            return json.dumps(await self.incoming.get())

        async def send(self, payload):
            event = json.loads(payload)
            if event["type"] == "session.close":
                self.closed_before_last_packet = self.audio_packets < 40
                self.bot_was_speaking_at_close = engine._bot_is_speaking
                await self.incoming.put({"type": "session.closed"})
            elif (
                event["type"] == "session.input_audio.append" and self.goodbye_requested
            ):
                self.audio_packets += 1
                # Delay the first audio, then stream it over many input packets.
                # A fast variable extractor must not close the session meanwhile.
                if self.audio_packets < 5:
                    return
                if self.audio_packets in {5, 40}:
                    await self.incoming.put(
                        {
                            "type": "session.output_transcript.delta",
                            "role": "assistant",
                            "delta": (
                                "No problem at all. Thank you for your "
                                if self.audio_packets == 5
                                else "time. Goodbye!"
                            ),
                        }
                    )
                pcm = b"\x00\x30" * 480 if self.audio_packets <= 40 else bytes(960)
                await self.incoming.put(
                    {
                        "type": "session.output_audio.delta",
                        "delta": base64.b64encode(pcm).decode(),
                    }
                )

        async def close(self):
            pass

    socket = ClosingAnnouncementSocket()
    service = DograhOpenAILiveLLMService(api_key="test", backend_model="gpt-5.4-mini")
    context = LLMContext()
    service._context = context
    # Begin in an established conversation, just before the end-node transition.
    service._session_started = True
    service._session_started_on_connection = True
    service._needs_session_config = False
    engine = PipecatEngine(
        llm=service,
        context=context,
        workflow=three_node_workflow_no_variable_extraction,
        call_context_vars={},
        is_realtime=True,
    )
    engine.active_agent.current_node = engine.active_agent.workflow.nodes["agent"]
    engine._perform_variable_extraction_if_needed = AsyncMock()
    engine.perform_final_variable_extraction = AsyncMock()
    turns, _ = _create_realtime_user_turn_config("openai_realtime", "gpt-live-1")
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=turns,
            user_mute_strategies=[
                CallbackUserMuteStrategy(should_mute_callback=engine.should_mute_user)
            ],
        ),
        realtime_service_mode=False,
    )
    transport = MockTransport(
        TransportParams(audio_out_enabled=True), generate_audio=True
    )
    pipeline = Pipeline(
        [
            transport.input(),
            aggregators.user(),
            service,
            transport.output(),
            aggregators.assistant(),
        ]
    )
    worker = PipelineWorker(pipeline, params=PipelineParams(), enable_rtvi=False)
    engine.call_worker = worker
    callbacks = []

    @worker.event_handler("on_pipeline_started")
    async def transition_to_end(_worker, _frame):
        transition = await engine._create_transition_func("move_to_end_call", "end")

        async def result_callback(_result, *, properties):
            # The provider starts its closing response after the tool result;
            # the assistant aggregator schedules this callback independently.
            socket.goodbye_requested = True
            callbacks.append(asyncio.create_task(properties.on_context_updated()))

        await transition(SimpleNamespace(arguments={}, result_callback=result_callback))

    try:
        with patch(
            "pipecat.services.openai.live.llm.websocket_connect",
            AsyncMock(return_value=socket),
        ):
            await asyncio.wait_for(run_pipeline_worker(worker), timeout=5)
        assert socket.closed_before_last_packet is False
        assert socket.bot_was_speaking_at_close is False
        assert any(
            message.get("role") == "assistant"
            and "time. Goodbye!" in message.get("content", "")
            for message in context.messages
        )
        engine.perform_final_variable_extraction.assert_awaited_once()
    finally:
        for callback in callbacks:
            if not callback.done():
                callback.cancel()
        await asyncio.gather(*callbacks, return_exceptions=True)
