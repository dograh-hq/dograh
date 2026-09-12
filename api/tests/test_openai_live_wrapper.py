from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    LLMMessagesAppendFrame,
    MetricsFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM, LLMService
from pipecat.services.openai.live import events
from pipecat.services.openai.live.llm import OpenAILiveLLMService
from pipecat.services.settings import LLMSettings

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
async def test_static_greeting_waits_for_session_and_idle_prompts_stay_out_of_local_context():
    service = make_service()
    service._context = LLMContext()
    await service.process_frame(TTSSpeakFrame("Bonjour!"), FrameDirection.DOWNSTREAM)
    assert sent_events(service, "session.commentary.append") == []
    await start_session(service)
    assert "Bonjour!" in sent_events(service, "session.commentary.append")[0]["content"]
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
        in sent_events(service, "session.commentary.append")[-1]["content"]
    )
    assert service._context.messages == []


@pytest.mark.asyncio
async def test_mute_gates_audio_without_disabling_unmuted_full_duplex_input():
    service = make_service()
    service._session_started = True
    audio = InputAudioRawFrame(b"\x00\x01" * 480, 24000, 1)
    await service.process_frame(UserMuteStartedFrame(), FrameDirection.DOWNSTREAM)
    await service._send_user_audio(audio)
    assert sent_events(service, "session.input_audio.append") == []
    await service.process_frame(UserMuteStoppedFrame(), FrameDirection.DOWNSTREAM)
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await service._send_user_audio(audio)
    assert len(sent_events(service, "session.input_audio.append")) == 1


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
