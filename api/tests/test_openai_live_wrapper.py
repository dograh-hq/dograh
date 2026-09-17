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
    CancelFrame,
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
        ("gpt-realtime-2.1-mini", DograhOpenAIRealtimeLLMService),
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
        assert service._delegation.settings.model == "gpt-5.6-luna"
    else:
        assert config.realtime.voice == "alloy"


def test_live_service_uses_live_endpoint_with_byok_key_and_backend_model():
    """Regression: gpt-live-1 must reach the Live API, never /v1/realtime."""
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_realtime",
                "api_key": "byok-live-key",
                "model": "gpt-live-1",
            },
        }
    )
    service = create_realtime_llm_service(config, SimpleNamespace())
    assert isinstance(service, DograhOpenAILiveLLMService)
    assert not isinstance(service, DograhOpenAIRealtimeLLMService)
    assert service.base_url == "wss://api.openai.com/v1/live/sessions"
    assert "realtime" not in service.base_url
    assert service._session_model == "gpt-live-1"
    assert service.api_key == "byok-live-key"
    assert service._settings.voice == "marin"
    assert service._delegation.settings.model == "gpt-5.6-luna"


@pytest.mark.asyncio
async def test_live_session_start_carries_live_model_not_realtime():
    service = create_realtime_llm_service(
        EffectiveAIModelConfiguration.model_validate(
            {
                "is_realtime": True,
                "realtime": {
                    "provider": "openai_realtime",
                    "api_key": "byok-live-key",
                    "model": "gpt-live-1",
                },
            }
        ),
        SimpleNamespace(),
    )
    service.send_client_event = AsyncMock()
    await service._handle_context(LLMContext())
    session = sent_events(service, "session.start")[0]["session"]
    assert session["model"] == "gpt-live-1"


def test_live_model_fails_clearly_without_live_implementation():
    """If the Pipecat revision lacks openai.live, fail with a clear 500 (no key leak)."""
    import sys

    from fastapi import HTTPException

    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_realtime",
                "api_key": "byok-live-key",
                "model": "gpt-live-1",
            },
        }
    )
    with (
        patch.dict(sys.modules, {"api.services.pipecat.realtime.openai_live": None}),
        pytest.raises(HTTPException) as exc_info,
    ):
        create_realtime_llm_service(config, SimpleNamespace())
    assert exc_info.value.status_code == 500
    assert "gpt-live-1" in exc_info.value.detail
    assert "byok-live-key" not in exc_info.value.detail


@pytest.mark.parametrize(
    "model",
    ["gpt-realtime-2", "gpt-realtime-2.1", "gpt-realtime-2.1-mini"],
)
def test_realtime_models_use_realtime_endpoint_with_byok_key(model):
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_realtime",
                "api_key": "byok-realtime-key",
                "model": model,
            },
        }
    )
    service = create_realtime_llm_service(config, SimpleNamespace())
    assert isinstance(service, DograhOpenAIRealtimeLLMService)
    assert service.base_url == f"wss://api.openai.com/v1/realtime?model={model}"
    assert service.api_key == "byok-realtime-key"


def test_live_turns_do_not_enable_local_interruptions():
    strategies, vad = _create_realtime_user_turn_config("openai_realtime", "gpt-live-1")
    assert vad is None
    assert strategies.start[0]._enable_interruptions is False
    assert strategies.stop[0].wait_for_transcript is False
    assert OpenAIRealtimeLLMConfiguration(api_key="test").model == "gpt-realtime-2"


def _live_service_with_workflow_tools():
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_realtime",
                "api_key": "byok-live-key",
                "model": "gpt-live-1",
            },
        }
    )
    service = create_realtime_llm_service(config, SimpleNamespace())
    service.register_function("ordinary_enquiry", AsyncMock(), is_node_transition=True)
    service.register_function("safety_concern", AsyncMock(), is_node_transition=True)
    service.register_function("lookup", AsyncMock())
    return service


def _responses_tool_payload():
    return [
        {
            "type": "function",
            "name": "ordinary_enquiry",
            "description": "transition",
            "parameters": {},
        },
        {
            "type": "function",
            "name": "safety_concern",
            "description": "transition",
            "parameters": {},
        },
        {
            "type": "function",
            "name": "lookup",
            "description": "ordinary tool",
            "parameters": {},
        },
        {"type": "web_search"},
    ]


@pytest.mark.asyncio
async def test_initial_turn_withholds_transition_tools_and_constrains_backend():
    from api.services.pipecat.realtime.openai_live import INITIAL_TURN_RULE

    service = _live_service_with_workflow_tools()
    assert service._awaiting_caller_input is True
    await service._update_settings(LLMSettings(system_instruction="Say hello."))
    backend_instructions = service._delegation.settings.system_instruction
    assert INITIAL_TURN_RULE in backend_instructions
    assert "Say hello." in backend_instructions
    delegation = service._delegation_config(_responses_tool_payload(), None)
    names = [
        tool.get("name")
        for tool in delegation.responses["tools"]
        if isinstance(tool, dict)
    ]
    assert "ordinary_enquiry" not in names
    assert "safety_concern" not in names
    assert "lookup" in names
    assert any(
        isinstance(tool, dict) and tool.get("type") == "web_search"
        for tool in delegation.responses["tools"]
    )


@pytest.mark.asyncio
async def test_first_caller_utterance_releases_transition_tools():
    from pipecat.utils.asyncio.task_manager import TaskManager

    service = _live_service_with_workflow_tools()
    service._task_manager = TaskManager()
    service._context = LLMContext(
        tools=ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="ordinary_enquiry",
                    description="transition",
                    properties={},
                    required=[],
                ),
                FunctionSchema(
                    name="lookup", description="lookup", properties={}, required=[]
                ),
            ]
        )
    )
    service._session_started = True
    service.send_client_event = AsyncMock()
    await service._update_settings(LLMSettings(system_instruction="Say hello."))
    service.send_client_event.reset_mock()
    assert service._awaiting_caller_input is True
    await service._handle_evt_transcript_delta(
        events.TranscriptDeltaEvent(
            type="session.input_transcript.delta",
            delta="Hi, my heater is broken",
        )
    )
    assert service._awaiting_caller_input is False
    updates = [
        call.args[0].to_payload()
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.update"
    ]
    assert updates, "expected a session.update publishing the full tool set"
    names = [
        tool.get("name")
        for tool in updates[-1]["session"]["delegation"]["responses"]["tools"]
        if isinstance(tool, dict)
    ]
    assert "ordinary_enquiry" in names
    assert "lookup" in names


@pytest.mark.asyncio
async def test_assistant_transcript_does_not_release_initial_turn_guard():
    from pipecat.utils.asyncio.task_manager import TaskManager

    service = _live_service_with_workflow_tools()
    service._task_manager = TaskManager()
    service._context = LLMContext()
    service._session_started = True
    service.send_client_event = AsyncMock()
    await service._handle_evt_transcript_delta(
        events.TranscriptDeltaEvent(
            type="session.output_transcript.delta",
            delta="Hi! How can I help",
        )
    )
    assert service._awaiting_caller_input is True
    assert not [
        call
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.update"
    ]


def test_live_default_backend_model_is_luna():
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_realtime",
                "api_key": "byok-live-key",
                "model": "gpt-live-1",
            },
        }
    )
    service = create_realtime_llm_service(config, SimpleNamespace())
    assert service._delegation.settings.model == "gpt-5.6-luna"


def _live_config_with(**realtime_overrides):
    realtime = {
        "provider": "openai_realtime",
        "api_key": "byok-live-key",
        "model": "gpt-live-1",
    }
    realtime.update(realtime_overrides)
    return EffectiveAIModelConfiguration.model_validate(
        {"is_realtime": True, "realtime": realtime}
    )


def _escalation_service(*transition_names):
    service = create_realtime_llm_service(_live_config_with(), SimpleNamespace())
    for name in transition_names:
        service.register_function(name, AsyncMock(), is_node_transition=True)
    service.register_function("lookup", AsyncMock())
    service._awaiting_caller_input = False
    return service


def _completed_envelope():
    from types import SimpleNamespace

    return SimpleNamespace(event={"type": "response.completed"})


def _function_call_envelope(name="ordinary_enquiry"):
    from types import SimpleNamespace

    return SimpleNamespace(
        event={
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "name": name,
                "call_id": "call-1",
                "status": "completed",
            },
        }
    )


@pytest.mark.asyncio
async def test_tool_choice_stays_auto_during_opening_guard():
    service = create_realtime_llm_service(_live_config_with(), SimpleNamespace())
    service.register_function("ordinary_enquiry", AsyncMock(), is_node_transition=True)
    assert service._awaiting_caller_input is True
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        for _ in range(5):
            await service._handle_evt_response(_completed_envelope())
    assert service._backend_responses_without_calls == 0
    tools = [{"type": "function", "name": "ordinary_enquiry", "parameters": {}}]
    assert service._escalated_tool_choice(tools) is None


@pytest.mark.asyncio
async def test_tool_choice_auto_below_threshold():
    service = _escalation_service("ordinary_enquiry")
    tools = [{"type": "function", "name": "ordinary_enquiry", "parameters": {}}]
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        await service._handle_evt_response(_completed_envelope())
        await service._handle_evt_response(_completed_envelope())
    assert service._backend_responses_without_calls == 2
    assert service._escalated_tool_choice(tools) is None


@pytest.mark.asyncio
async def test_single_transition_forced_by_name():
    service = _escalation_service("ordinary_enquiry")
    tools = [
        {"type": "function", "name": "ordinary_enquiry", "parameters": {}},
        {"type": "function", "name": "lookup", "parameters": {}},
    ]
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        for _ in range(3):
            await service._handle_evt_response(_completed_envelope())
    assert service._escalated_tool_choice(tools) == {
        "type": "function",
        "name": "ordinary_enquiry",
    }


@pytest.mark.asyncio
async def test_multiple_transitions_require_tool_call():
    service = _escalation_service("ordinary_enquiry", "safety_concern")
    tools = [
        {"type": "function", "name": "ordinary_enquiry", "parameters": {}},
        {"type": "function", "name": "safety_concern", "parameters": {}},
    ]
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        for _ in range(3):
            await service._handle_evt_response(_completed_envelope())
    assert service._escalated_tool_choice(tools) == "required"


@pytest.mark.asyncio
async def test_safety_transition_forced_by_name():
    service = _escalation_service("safety_concern")
    tools = [{"type": "function", "name": "safety_concern", "parameters": {}}]
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        for _ in range(4):
            await service._handle_evt_response(_completed_envelope())
    assert service._escalated_tool_choice(tools) == {
        "type": "function",
        "name": "safety_concern",
    }


@pytest.mark.asyncio
async def test_terminal_node_stays_auto():
    service = _escalation_service()
    tools = [{"type": "function", "name": "lookup", "parameters": {}}]
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        for _ in range(5):
            await service._handle_evt_response(_completed_envelope())
    assert service._backend_responses_without_calls == 5
    assert service._escalated_tool_choice(tools) is None
    assert service._escalated_tool_choice([]) is None


@pytest.mark.asyncio
async def test_function_call_resets_liveness_counter():
    service = _escalation_service("ordinary_enquiry")
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        await service._handle_evt_response(_completed_envelope())
        await service._handle_evt_response(_completed_envelope())
        await service._handle_evt_response(_function_call_envelope("ordinary_enquiry"))
    assert service._backend_responses_without_calls == 0


@pytest.mark.asyncio
async def test_escalation_propagates_via_session_update():
    from pipecat.utils.asyncio.task_manager import TaskManager

    service = _escalation_service("ordinary_enquiry")
    service._task_manager = TaskManager()
    service._context = LLMContext(
        tools=ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="ordinary_enquiry",
                    description="transition",
                    properties={},
                    required=[],
                ),
            ]
        )
    )
    service._session_started = True
    service.send_client_event = AsyncMock()
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        for _ in range(3):
            await service._handle_evt_response(_completed_envelope())
    updates = [
        call.args[0].to_payload()
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.update"
    ]
    assert updates, "expected escalation to publish a session.update"
    assert updates[-1]["session"]["delegation"]["responses"]["tool_choice"] == {
        "type": "function",
        "name": "ordinary_enquiry",
    }


def test_reasoning_effort_default_is_low():
    service = create_realtime_llm_service(_live_config_with(), SimpleNamespace())
    assert service._delegation.settings.reasoning.effort == "low"


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_reasoning_effort_options_reach_backend(effort):
    service = create_realtime_llm_service(
        _live_config_with(reasoning_effort=effort), SimpleNamespace()
    )
    assert service._delegation.settings.reasoning.effort == effort
    delegation = service._delegation_config([], None)
    assert delegation.responses["reasoning"] == {"effort": effort}


def test_web_search_disabled_by_default():
    service = create_realtime_llm_service(_live_config_with(), SimpleNamespace())
    assert service._web_search_enabled is False
    tools = service._backend_tools(
        [{"type": "function", "name": "lookup", "parameters": {}}]
    )
    assert all(
        not (isinstance(tool, dict) and tool.get("type") == "web_search")
        for tool in tools
    )


def test_web_search_enabled_includes_tool():
    service = create_realtime_llm_service(
        _live_config_with(web_search=True), SimpleNamespace()
    )
    assert service._web_search_enabled is True
    service.register_function("lookup", AsyncMock())
    tools = service._backend_tools(
        [{"type": "function", "name": "lookup", "parameters": {}}]
    )
    assert {"type": "web_search"} in tools
    delegation = service._delegation_config(
        [{"type": "function", "name": "lookup", "parameters": {}}], None
    )
    assert {"type": "web_search"} in delegation.responses["tools"]


@pytest.mark.asyncio
async def test_cancel_sends_session_close_before_disconnect():
    service = make_service()
    service._session_started = True
    service._websocket = AsyncMock()
    service.send_client_event = AsyncMock()
    with patch.object(
        OpenAILiveLLMService, "cancel", new_callable=AsyncMock
    ) as super_cancel:
        await service.cancel(CancelFrame())
    closes = [
        call.args[0]
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.close"
    ]
    assert len(closes) == 1
    super_cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_without_session_skips_close():
    service = make_service()
    service._session_started = False
    service._websocket = AsyncMock()
    service.send_client_event = AsyncMock()
    with patch.object(OpenAILiveLLMService, "cancel", new_callable=AsyncMock):
        await service.cancel(CancelFrame())
    assert not [
        call
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.close"
    ]


@pytest.mark.asyncio
async def test_engine_goodbye_text_spoken_via_live_session(simple_workflow):
    service = make_service()
    service._session_started = True
    service.send_client_event = AsyncMock()
    engine = PipecatEngine(
        llm=service,
        context=LLMContext(),
        workflow=simple_workflow,
        call_context_vars={},
        is_realtime=True,
    )
    played = await engine.queue_text_message("Goodbye now.")
    assert played is True
    appends = [
        call.args[0]
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.instructions.append"
    ]
    assert appends, "expected the goodbye to reach the Live session"


@pytest.mark.asyncio
async def test_live_backend_receives_extraction_field_metadata(simple_workflow):
    service = make_service()
    service.send_client_event = AsyncMock()
    engine = PipecatEngine(
        llm=service,
        context=LLMContext(),
        workflow=simple_workflow,
        call_context_vars={},
        is_realtime=True,
    )
    await engine.set_node("start", emit_transition_event=False)
    instructions = service._delegation.settings.system_instruction
    assert "user_intent" in instructions
    assert "string" in instructions


@pytest.mark.asyncio
async def test_realtime_prompt_excludes_extraction_section(simple_workflow):
    from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService

    service = DograhOpenAIRealtimeLLMService(
        api_key="test",
        settings=OpenAIRealtimeLLMService.Settings(model="gpt-realtime-2"),
    )
    engine = PipecatEngine(
        llm=service,
        context=LLMContext(),
        workflow=simple_workflow,
        call_context_vars={},
        is_realtime=True,
    )
    await engine.set_node("start", emit_transition_event=False)
    assert "Structured data to collect" not in service._settings.system_instruction


def test_live_language_constraint_flows_to_voice_and_backend():
    service = create_realtime_llm_service(
        _live_config_with(language="es"), SimpleNamespace()
    )
    assert service._language == "es"
    assert "Conversation language" in service._settings.system_instruction


@pytest.mark.asyncio
async def test_live_backend_instructions_carry_language_constraint():
    service = create_realtime_llm_service(
        _live_config_with(language="pt"), SimpleNamespace()
    )
    await service._update_settings(LLMSettings(system_instruction="Say hello."))
    assert "Conversation language" in service._delegation.settings.system_instruction
    assert "pt" in service._delegation.settings.system_instruction


def test_live_without_language_has_no_language_constraint():
    service = create_realtime_llm_service(_live_config_with(), SimpleNamespace())
    assert service._language is None
    assert "Conversation language" not in service._settings.system_instruction


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
    # Live speaks transition text through the session (no TTS frame, no mute).
    appends = sent_events(service, "session.instructions.append")
    assert len(appends) == 1
    assert "Let me ask a few questions." in appends[0]["content"]
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


@pytest.mark.asyncio
async def test_pending_extraction_cancelled_logged_distinctly():
    from loguru import logger as loguru_logger

    from api.services.workflow.pipecat_engine import PipecatEngine

    async def ok():
        return {"a": 1}

    async def boom():
        raise ValueError("bad extraction")

    async def cancelled():
        await asyncio.sleep(30)
        return {}

    engine = PipecatEngine.__new__(PipecatEngine)
    loop = asyncio.get_running_loop()
    t_ok = loop.create_task(ok(), name="variable-extraction:ok-node")
    t_boom = loop.create_task(boom(), name="variable-extraction:bad-node")
    t_cancel = loop.create_task(cancelled(), name="variable-extraction:gone-node")
    await asyncio.sleep(0)
    t_cancel.cancel()
    engine._pending_extraction_tasks = {t_ok, t_boom, t_cancel}
    messages: list[str] = []
    handler_id = loguru_logger.add(messages.append, format="{message}")
    try:
        await engine._await_pending_extractions(timeout=5.0)
    finally:
        loguru_logger.remove(handler_id)
    text = "\n".join(messages)
    assert "was cancelled" in text
    assert "bad-node" in text and "bad extraction" in text


# ---------------------------------------------------------------------------
# Phase 5 compatibility workflow fixture + progression tests (Phase 1 B/E/F/G)
# ---------------------------------------------------------------------------


def _compatibility_workflow():
    from api.services.workflow.dto import (
        AgentNodeData,
        EdgeDataDTO,
        EndCallNodeData,
        Position,
        ReactFlowDTO,
        RFEdgeDTO,
        RFNodeDTO,
        StartCallNodeData,
    )
    from api.services.workflow.workflow_graph import WorkflowGraph

    dto = ReactFlowDTO(
        nodes=[
            RFNodeDTO(
                id="start",
                type="startCall",
                position=Position(x=0, y=0),
                data=StartCallNodeData(
                    name="Opening",
                    prompt="Open the call and ask what the caller needs.",
                    is_start=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=True,
                    extraction_prompt="Extract the greeting type.",
                    extraction_variables=[],
                ),
            ),
            RFNodeDTO(
                id="branch_a",
                type="agentNode",
                position=Position(x=0, y=200),
                data=AgentNodeData(
                    name="Issue A Branch",
                    prompt="Handle issue A.",
                    allow_interrupt=False,
                    add_global_prompt=False,
                ),
            ),
            RFNodeDTO(
                id="branch_b",
                type="agentNode",
                position=Position(x=400, y=200),
                data=AgentNodeData(
                    name="Issue B Branch",
                    prompt="Handle issue B.",
                    allow_interrupt=False,
                    add_global_prompt=False,
                ),
            ),
            RFNodeDTO(
                id="escalation",
                type="agentNode",
                position=Position(x=800, y=200),
                data=AgentNodeData(
                    name="Escalation",
                    prompt="Handle the safety escalation.",
                    allow_interrupt=False,
                    add_global_prompt=False,
                ),
            ),
            RFNodeDTO(
                id="terminal",
                type="endCall",
                position=Position(x=0, y=400),
                data=EndCallNodeData(
                    name="Terminal",
                    prompt="Say goodbye.",
                    is_end=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="start-a",
                source="start",
                target="branch_a",
                data=EdgeDataDTO(
                    label="Issue A",
                    condition="Choose when the caller describes issue A.",
                ),
            ),
            RFEdgeDTO(
                id="start-b",
                source="start",
                target="branch_b",
                data=EdgeDataDTO(
                    label="Issue B",
                    condition="Choose when the caller describes issue B.",
                ),
            ),
            RFEdgeDTO(
                id="start-safety",
                source="start",
                target="escalation",
                data=EdgeDataDTO(
                    label="Safety concern",
                    condition="Choose when the caller reports immediate danger.",
                ),
            ),
            RFEdgeDTO(
                id="a-terminal",
                source="branch_a",
                target="terminal",
                data=EdgeDataDTO(
                    label="Finish A",
                    condition="Choose when issue A is resolved.",
                ),
            ),
            RFEdgeDTO(
                id="b-terminal",
                source="branch_b",
                target="terminal",
                data=EdgeDataDTO(
                    label="Finish B",
                    condition="Choose when issue B is resolved.",
                ),
            ),
            RFEdgeDTO(
                id="escalation-terminal",
                source="escalation",
                target="terminal",
                data=EdgeDataDTO(
                    label="Safety done",
                    condition="Choose when safety guidance is delivered.",
                ),
            ),
        ],
    )
    return WorkflowGraph(dto)


def _live_engine(service, workflow):
    task = SimpleNamespace(queue_frame=AsyncMock())
    engine = PipecatEngine(
        llm=service,
        context=LLMContext(),
        task=task,
        workflow=workflow,
        call_context_vars={},
        is_realtime=True,
    )
    return engine, task


@pytest.mark.asyncio
async def test_competing_transitions_execute_exactly_once():
    service = make_service()
    service.send_client_event = AsyncMock()
    engine, _task = _live_engine(service, _compatibility_workflow())
    from api.services.workflow.pipecat_engine_variable_extractor import (
        VariableExtractionManager,
    )

    engine._variable_extraction_manager = VariableExtractionManager(engine)
    await engine.set_node("start", emit_transition_event=False)
    assert service.has_function("issue_a")
    assert service.has_function("issue_b")
    transition_a = await engine._create_transition_func("issue_a", "branch_a")
    await transition_a(SimpleNamespace(arguments={}, result_callback=AsyncMock()))
    assert engine.active_agent.current_node.id == "branch_a"
    assert engine._gathered_context["nodes_visited"] == ["Opening", "Issue A Branch"]


@pytest.mark.asyncio
async def test_safety_path_avoids_ordinary_branches():
    service = make_service()
    service.send_client_event = AsyncMock()
    engine, _task = _live_engine(service, _compatibility_workflow())
    from api.services.workflow.pipecat_engine_variable_extractor import (
        VariableExtractionManager,
    )

    engine._variable_extraction_manager = VariableExtractionManager(engine)
    await engine.set_node("start", emit_transition_event=False)
    transition = await engine._create_transition_func("safety_concern", "escalation")
    await transition(SimpleNamespace(arguments={}, result_callback=AsyncMock()))
    assert engine.active_agent.current_node.id == "escalation"
    assert "Issue A Branch" not in engine._gathered_context["nodes_visited"]
    assert "Issue B Branch" not in engine._gathered_context["nodes_visited"]
    finish = await engine._create_transition_func("safety_done", "terminal")
    await finish(SimpleNamespace(arguments={}, result_callback=AsyncMock()))
    assert engine.active_agent.current_node.id == "terminal"


@pytest.mark.asyncio
async def test_terminal_transition_queues_single_endframe():
    from pipecat.frames.frames import EndFrame

    service = make_service()
    service.send_client_event = AsyncMock()
    engine, task = _live_engine(service, _compatibility_workflow())
    from api.services.workflow.pipecat_engine_variable_extractor import (
        VariableExtractionManager,
    )

    engine._variable_extraction_manager = VariableExtractionManager(engine)
    await engine.set_node("branch_a", emit_transition_event=False)
    finish = await engine._create_transition_func("finish_a", "terminal")
    captured = {}

    async def result_callback(result, properties=None):
        captured["props"] = properties

    await finish(SimpleNamespace(arguments={}, result_callback=result_callback))
    assert engine.active_agent.current_node.id == "terminal"
    # Simulate the aggregator firing on_context_updated after the closing
    # response reaches the caller.
    await captured["props"].on_context_updated()
    endframes = [
        call.args[0]
        for call in task.queue_frame.await_args_list
        if isinstance(call.args[0], EndFrame)
    ]
    assert len(endframes) == 1
    assert not [
        call
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "response.create"
    ]


@pytest.mark.asyncio
async def test_arbitrary_tool_arguments_round_trip():
    from pipecat.utils.asyncio.task_manager import TaskManager

    service = make_service()
    service._task_manager = TaskManager()
    service._setup = SimpleNamespace(
        enable_metrics=False,
        enable_usage_metrics=False,
        enable_tracing=False,
        pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
    )
    service.send_client_event = AsyncMock()
    service.push_frame = AsyncMock()
    service.broadcast_frame = AsyncMock()
    received = {}

    async def echo(params, **kwargs):
        received.update(params.arguments)
        await params.result_callback(
            {
                "expression": params.arguments["expression"],
                "count": params.arguments["count"],
                "flag": params.arguments["flag"],
                "options": params.arguments["options"],
            }
        )

    service.register_function("calculate", echo)
    call = FunctionCallFromLLM(
        function_name="calculate",
        tool_call_id="call-args-1",
        arguments={
            "expression": "1+2",
            "count": 3,
            "flag": True,
            "options": {"mode": "fast"},
        },
        context=LLMContext(),
    )
    await service.run_function_calls([call])
    results = []
    for _ in range(100):
        if received == {
            "expression": "1+2",
            "count": 3,
            "flag": True,
            "options": {"mode": "fast"},
        }:
            break
        await asyncio.sleep(0.05)
    assert received == {
        "expression": "1+2",
        "count": 3,
        "flag": True,
        "options": {"mode": "fast"},
    }
    results = []
    for _ in range(100):
        results = [
            call
            for call in service.broadcast_frame.await_args_list
            if call.args and call.args[0].__name__ == "FunctionCallResultFrame"
        ]
        if results:
            break
        await asyncio.sleep(0.05)
    assert results, "expected a result frame for the tool call"
    import json as _json

    result = results[0].kwargs.get("result")
    parsed = _json.loads(result) if isinstance(result, str) else result
    assert parsed["count"] == 3 and isinstance(parsed["count"], int)
    assert parsed["flag"] is True
    assert parsed["options"] == {"mode": "fast"}


@pytest.mark.asyncio
async def test_live_extraction_writes_gathered_context(simple_workflow):
    from api.services.workflow.pipecat_engine_variable_extractor import (
        VariableExtractionManager,
    )

    service = make_service()
    service.send_client_event = AsyncMock()
    engine = PipecatEngine(
        llm=service,
        context=LLMContext(),
        task=SimpleNamespace(queue_frame=AsyncMock()),
        workflow=simple_workflow,
        call_context_vars={},
        is_realtime=True,
    )
    engine._variable_extraction_manager = VariableExtractionManager(engine)
    engine.active_agent.variable_extraction_llm = SimpleNamespace(
        run_inference=AsyncMock(return_value='{"user_intent": "billing"}'),
        model_name="stub",
    )
    await engine.set_node("start", emit_transition_event=False)
    engine.context.add_message({"role": "user", "content": "I need help with my bill"})
    result = await engine._perform_variable_extraction_if_needed(
        simple_workflow.nodes["start"], run_in_background=False
    )
    assert result == {"user_intent": "billing"}
    assert engine._gathered_context["user_intent"] == "billing"
    assert engine._gathered_context["extracted_variables"] == {"user_intent": "billing"}


@pytest.mark.asyncio
async def test_interruption_during_speech_defers_then_runs_once():
    from pipecat.utils.asyncio.task_manager import TaskManager

    service = make_service()
    service._task_manager = TaskManager()
    service._setup = SimpleNamespace(
        enable_metrics=False,
        enable_usage_metrics=False,
        enable_tracing=False,
        pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
        observer=None,
    )
    service._session_started = True
    service._context = LLMContext()
    service.send_client_event = AsyncMock()
    service.push_frame = AsyncMock()
    handler = AsyncMock()
    service.register_function("ordinary_enquiry", handler, is_node_transition=True)
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    call = FunctionCallFromLLM(
        function_name="ordinary_enquiry",
        tool_call_id="call-defer-1",
        arguments={},
        context=LLMContext(),
    )
    await service.run_function_calls([call])
    handler.assert_not_awaited()
    await service._handle_evt_transcript_delta(
        events.TranscriptDeltaEvent(
            type="session.input_transcript.delta",
            delta="wait, actually",
        )
    )
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    for _ in range(100):
        if handler.await_count >= 1:
            break
        await asyncio.sleep(0.05)
    handler.assert_awaited_once()
    assert not [
        call
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "response.create"
    ]


@pytest.mark.asyncio
async def test_cancel_during_delegation_sends_single_close_and_clears():
    service = make_service()
    service._session_started = True
    service._websocket = AsyncMock()
    service.send_client_event = AsyncMock()
    service._deferred_transitions = ["stale"]
    with patch.object(OpenAILiveLLMService, "cancel", new_callable=AsyncMock):
        await service.cancel(CancelFrame())
        await service.cancel(CancelFrame())
    closes = [
        call.args[0]
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.close"
    ]
    assert len(closes) == 1


@pytest.mark.parametrize(
    "provider,model,expect_mute_during_tools",
    [
        ("openai_realtime", "gpt-live-1", False),
        ("openai_realtime", "gpt-realtime-2", True),
        ("openai_realtime", "gpt-realtime-2.1", True),
        ("google_realtime", "gemini-3.1-flash-live-preview", True),
        (None, None, True),
    ],
)
def test_live_caller_not_muted_during_function_execution(
    provider, model, expect_mute_during_tools
):
    from pipecat.turns.user_mute import FunctionCallUserMuteStrategy

    from api.services.pipecat.run_pipeline import _create_user_mute_strategies

    engine = SimpleNamespace(should_mute_user=AsyncMock())
    strategies = _create_user_mute_strategies(
        engine, None, realtime_provider=provider, realtime_model=model
    )
    assert (
        any(isinstance(s, FunctionCallUserMuteStrategy) for s in strategies)
        is expect_mute_during_tools
    )


def _stale_service():
    service = make_service()
    service.register_function("ordinary_enquiry", AsyncMock(), is_node_transition=True)
    service._context = LLMContext()
    return service


@pytest.mark.asyncio
async def test_current_transition_executes_with_arguments():
    from pipecat.utils.asyncio.task_manager import TaskManager

    service = _stale_service()
    service._task_manager = TaskManager()
    service._setup = SimpleNamespace(
        enable_metrics=False,
        enable_usage_metrics=False,
        enable_tracing=False,
        pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
        observer=None,
    )
    service.push_frame = AsyncMock()
    service.broadcast_frame = AsyncMock()
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        await service._handle_evt_response(
            SimpleNamespace(
                event={
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "ordinary_enquiry",
                        "call_id": "call-now",
                        "status": "completed",
                    },
                }
            )
        )
    assert service._call_revisions.get("call-now") == service._node_revision
    call = FunctionCallFromLLM(
        function_name="ordinary_enquiry",
        tool_call_id="call-now",
        arguments={"reason": "caller confirmed"},
        context=LLMContext(),
    )
    await service.run_function_calls([call])
    for _ in range(100):
        if service._functions["ordinary_enquiry"].handler.await_count >= 1:
            break
        await asyncio.sleep(0.05)
    service._functions["ordinary_enquiry"].handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_node_transition_dropped_after_set_node():
    service = _stale_service()
    service._context = LLMContext(
        tools=ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="ordinary_enquiry",
                    description="transition",
                    properties={},
                    required=[],
                ),
            ]
        )
    )
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        await service._handle_evt_response(
            SimpleNamespace(
                event={
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "ordinary_enquiry",
                        "call_id": "call-old",
                        "status": "completed",
                    },
                }
            )
        )
    await service._update_settings(LLMSettings(system_instruction="Next node."))
    call = FunctionCallFromLLM(
        function_name="ordinary_enquiry",
        tool_call_id="call-old",
        arguments={},
        context=LLMContext(),
    )
    await service.run_function_calls([call])
    service._functions["ordinary_enquiry"].handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_unadvertised_function_dropped():
    service = _stale_service()
    service._context = LLMContext(
        tools=ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="lookup", description="tool", properties={}, required=[]
                ),
            ]
        )
    )
    call = FunctionCallFromLLM(
        function_name="ordinary_enquiry",
        tool_call_id="call-ghost",
        arguments={},
        context=LLMContext(),
    )
    await service.run_function_calls([call])
    service._functions["ordinary_enquiry"].handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_session_drops_function_execution():
    service = _stale_service()
    service._terminal = True
    call = FunctionCallFromLLM(
        function_name="ordinary_enquiry",
        tool_call_id="call-term",
        arguments={"reason": "x"},
        context=LLMContext(),
    )
    await service.run_function_calls([call])
    service._functions["ordinary_enquiry"].handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_blocks_backend_continuation():
    service = make_service()
    service._terminal = True
    with patch.object(
        OpenAILiveLLMService, "_maybe_continue_response", new_callable=AsyncMock
    ) as cont:
        await service._maybe_continue_response("key-1")
    cont.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_blocks_context_appends():
    service = make_service()
    service.send_client_event = AsyncMock()
    service._terminal = True
    await service._send_context_append(None, "hello", spoken=False)
    await service._speak("hello")
    service.send_client_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_response_event_after_terminal_ignored():
    service = _stale_service()
    service._terminal = True
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        await service._handle_evt_response(
            SimpleNamespace(
                event={
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "ordinary_enquiry",
                        "call_id": "call-late",
                        "status": "completed",
                    },
                }
            )
        )
    call = FunctionCallFromLLM(
        function_name="ordinary_enquiry",
        tool_call_id="call-late",
        arguments={},
        context=LLMContext(),
    )
    await service.run_function_calls([call])
    service._functions["ordinary_enquiry"].handler.assert_not_awaited()


def _escalation_tools():
    return [
        {"type": "function", "name": "ordinary_enquiry", "parameters": {}},
        {"type": "function", "name": "lookup", "parameters": {}},
    ]


@pytest.mark.asyncio
async def test_escalation_then_deescalation_publishes_explicit_auto():
    from pipecat.utils.asyncio.task_manager import TaskManager

    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_realtime",
                "api_key": "byok-live-key",
                "model": "gpt-live-1",
            },
        }
    )
    service = create_realtime_llm_service(config, SimpleNamespace())
    service.register_function("ordinary_enquiry", AsyncMock(), is_node_transition=True)
    service.register_function("lookup", AsyncMock())
    assert service._awaiting_caller_input is True
    service._task_manager = TaskManager()
    service._context = LLMContext(
        tools=ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="ordinary_enquiry",
                    description="transition",
                    properties={},
                    required=[],
                ),
                FunctionSchema(
                    name="lookup", description="lookup", properties={}, required=[]
                ),
            ]
        )
    )
    service._session_started = True
    service.send_client_event = AsyncMock()
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        # Caller speaks: guard releases with full tools + explicit auto.
        await service._handle_evt_transcript_delta(
            events.TranscriptDeltaEvent(
                type="session.input_transcript.delta",
                delta="hello",
            )
        )
        assert service._awaiting_caller_input is False
        # Backend monologues without tools: escalate to the named transition.
        for _ in range(3):
            await service._handle_evt_response(_completed_envelope())
    escalated = [
        call.args[0].to_payload()
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.update"
    ]
    assert escalated, "expected escalation to publish a session.update"
    assert escalated[-1]["session"]["delegation"]["responses"]["tool_choice"] == {
        "type": "function",
        "name": "ordinary_enquiry",
    }
    # Caller speaks again: de-escalation must explicitly clear the choice
    # back to auto so the server cannot retain a stale required/named
    # choice once tools shrink or vanish.
    service.send_client_event.reset_mock()
    await service._handle_evt_transcript_delta(
        events.TranscriptDeltaEvent(
            type="session.input_transcript.delta",
            delta="thanks, bye",
        )
    )
    cleared = [
        call.args[0].to_payload()
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.update"
    ]
    assert cleared, "expected de-escalation to publish a session.update"
    assert cleared[-1]["session"]["delegation"]["responses"]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_terminal_update_carries_empty_tools_with_explicit_auto():
    from pipecat.utils.asyncio.task_manager import TaskManager

    service = _escalation_service("ordinary_enquiry")
    service._task_manager = TaskManager()
    service._context = LLMContext(
        tools=ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="ordinary_enquiry",
                    description="transition",
                    properties={},
                    required=[],
                ),
            ]
        )
    )
    service._session_started = True
    service.send_client_event = AsyncMock()
    with patch.object(
        OpenAILiveLLMService, "_handle_evt_response", new_callable=AsyncMock
    ):
        for _ in range(3):
            await service._handle_evt_response(_completed_envelope())
    # Terminal node: no tools advertised at all.
    service._context = LLMContext(tools=ToolsSchema(standard_tools=[]))
    service.send_client_event.reset_mock()
    await service._maybe_send_tools_update()
    updates = [
        call.args[0].to_payload()
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.update"
    ]
    assert updates, "expected the terminal tools update to be published"
    responses = updates[-1]["session"]["delegation"]["responses"]
    assert responses["tools"] == []
    # This exact shape (empty tools + explicit auto) is what the Live API
    # requires: required/named with zero tools is rejected server-side.
    assert responses["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_session_start_carries_explicit_auto_tool_choice():
    service = create_realtime_llm_service(_live_config_with(), SimpleNamespace())
    service.send_client_event = AsyncMock()
    await service._handle_context(LLMContext())
    starts = [
        call.args[0].to_payload()
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.start"
    ]
    assert starts, "expected session.start to be sent"
    # Explicit auto matches the documented server default; it only serves to
    # clear any previously retained choice on session updates.
    assert starts[0]["session"]["delegation"]["responses"]["tool_choice"] == "auto"


def test_transition_policy_requires_tool_call_on_satisfied_condition():
    from api.services.pipecat.realtime.openai_live import TRANSITION_POLICY

    assert "MUST call the" in TRANSITION_POLICY
    assert "corresponding transition tool" in TRANSITION_POLICY
    assert "actually moves the workflow" in TRANSITION_POLICY


def test_transition_policy_forbids_conversational_advance():
    from api.services.pipecat.realtime.openai_live import TRANSITION_POLICY

    assert (
        "Do not continue as though the workflow has advanced"
        in TRANSITION_POLICY.replace("\n", " ")
    )
    assert "before the transition occurs" in TRANSITION_POLICY.replace("\n", " ")


def test_transition_policy_covers_single_and_multiple_edges():
    from api.services.pipecat.realtime.openai_live import TRANSITION_POLICY

    flat = TRANSITION_POLICY.replace("\n", " ")
    assert "single transition" in flat
    assert "several transition tools" in flat
    assert "condition matches the caller" in flat


def test_transition_policy_guards_against_premature_calls():
    from api.services.pipecat.realtime.openai_live import TRANSITION_POLICY

    flat = TRANSITION_POLICY.replace("\n", " ")
    assert "Complete the current node" in flat
    assert "Do not call a transition merely because" in flat


@pytest.mark.asyncio
async def test_backend_instructions_compose_policy_prompt_and_turn_rule(
    simple_workflow,
):
    from api.services.pipecat.realtime.openai_live import (
        BACKEND_INSTRUCTIONS,
        INITIAL_TURN_RULE,
        TRANSITION_POLICY,
    )

    service = make_service()
    service.send_client_event = AsyncMock()
    engine = PipecatEngine(
        llm=service,
        context=LLMContext(),
        task=SimpleNamespace(queue_frame=AsyncMock()),
        workflow=simple_workflow,
        call_context_vars={},
        is_realtime=True,
    )
    from api.services.workflow.pipecat_engine_variable_extractor import (
        VariableExtractionManager,
    )

    engine._variable_extraction_manager = VariableExtractionManager(engine)
    await engine.set_node("start", emit_transition_event=False)
    instructions = service._delegation.settings.system_instruction
    assert BACKEND_INSTRUCTIONS in instructions
    assert TRANSITION_POLICY in instructions
    assert INITIAL_TURN_RULE in instructions
    assert "Start Call System Prompt" in instructions
    # Policy precedes node content; turn rule is conditional, policy permanent.
    assert instructions.index("MUST call the") < instructions.index(
        "Start Call System Prompt"
    )


def test_transition_policy_forbids_nonterminal_closing():
    from api.services.pipecat.realtime.openai_live import TRANSITION_POLICY

    flat = TRANSITION_POLICY.replace("\n", " ")
    assert "Do not speak closing, summary-of-completion, or goodbye content" in flat
    assert "while the current node is not terminal" in flat
    assert "only a transition or end-call tool does" in flat


def test_transition_policy_closing_guard_follows_next_node_rule():
    from api.services.pipecat.realtime.openai_live import TRANSITION_POLICY

    assert TRANSITION_POLICY.index(
        "do not simulate the next node"
    ) < TRANSITION_POLICY.index("Do not speak closing")
