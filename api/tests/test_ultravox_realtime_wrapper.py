import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import UltravoxRealtimeLLMConfiguration
from api.services.pipecat.realtime.ultravox_realtime import (
    DograhUltravoxOneShotInputParams,
    DograhUltravoxRealtimeLLMService,
)
from api.services.pipecat.service_factory import create_realtime_llm_service


class _ClosingSocket:
    def __init__(self, exc):
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._exc


class _MessageSocket:
    def __init__(self, messages):
        self._messages = iter(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration


def _make_service() -> DograhUltravoxRealtimeLLMService:
    service = DograhUltravoxRealtimeLLMService(
        params=DograhUltravoxOneShotInputParams(
            api_key="test-key",
            model="ultravox-v0.7",
            output_medium="voice",
        ),
        settings=DograhUltravoxRealtimeLLMService.Settings(
            model="ultravox-v0.7",
            output_medium="voice",
        ),
    )
    service.stop_all_metrics = AsyncMock()
    service.cancel_task = AsyncMock()
    service.push_error = AsyncMock()
    return service


def _tool_schema() -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="transition_to_next_node",
                description="Move to the next workflow node",
                properties={"reason": {"type": "string"}},
                required=[],
            )
        ]
    )


@pytest.mark.asyncio
async def test_tts_greeting_triggers_initial_connect():
    service = _make_service()
    service._connect_call = AsyncMock()

    await service.process_frame(
        TTSSpeakFrame("Hello there", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    service._connect_call.assert_awaited_once()
    assert service._connect_call.await_args.kwargs["greeting_text"] == "Hello there"
    assert service._connect_call.await_args.kwargs["agent_speaks_first"] is True


@pytest.mark.asyncio
async def test_initial_context_connects_without_replay():
    service = _make_service()
    service._connect_call = AsyncMock()
    context = LLMContext()

    await service._handle_context(context)

    service._connect_call.assert_awaited_once()
    assert service._connect_call.await_args.kwargs["greeting_text"] is None
    assert service._connect_call.await_args.kwargs["agent_speaks_first"] is True


@pytest.mark.asyncio
async def test_system_instruction_update_marks_stage_update_required():
    service = _make_service()
    service._socket = object()

    changed = await service._update_settings(
        DograhUltravoxRealtimeLLMService.Settings(system_instruction="new instruction")
    )

    assert "system_instruction" in changed
    assert service._stage_update_required is True


@pytest.mark.asyncio
async def test_node_transition_updates_native_stage_without_reconnecting():
    service = _make_service()
    service._socket = object()
    service._send = AsyncMock()
    service._connect_call = AsyncMock()
    service._pending_node_transition_tool_call_ids.add("call-transition")
    service._stage_update_required = True
    service._settings.system_instruction = "new instruction"

    context = LLMContext(
        messages=[
            {
                "role": "tool",
                "tool_call_id": "call-transition",
                "content": '{"status":"done"}',
            },
        ],
        tools=_tool_schema(),
    )

    await service._handle_context(context)

    service._connect_call.assert_not_awaited()
    service._send.assert_awaited_once()
    message = service._send.await_args.args[0]
    assert message["type"] == "client_tool_result"
    assert message["invocationId"] == "call-transition"
    assert message["responseType"] == "new-stage"
    stage = json.loads(message["result"])
    assert stage["systemPrompt"] == "new instruction"
    assert stage["toolResultText"] == '{"status":"done"}'
    assert stage["selectedTools"][0]["temporaryTool"]["modelToolName"] == (
        "transition_to_next_node"
    )
    assert "call-transition" in service._completed_tool_calls
    assert service._pending_node_transition_tool_call_ids == set()
    assert service._stage_update_required is False


@pytest.mark.asyncio
async def test_ordinary_tool_result_uses_standard_tool_response():
    service = _make_service()
    service._socket = object()
    service._send = AsyncMock()

    context = LLMContext(
        messages=[
            {
                "role": "tool",
                "tool_call_id": "call-transition",
                "content": '{"status":"done"}',
            },
        ],
        tools=_tool_schema(),
    )

    await service._handle_context(context)

    service._send.assert_awaited_once_with(
        {
            "type": "client_tool_result",
            "invocationId": "call-transition",
            "result": '{"status":"done"}',
        }
    )


@pytest.mark.asyncio
async def test_only_registered_node_transition_invocations_are_tracked():
    service = _make_service()
    service.run_function_calls = AsyncMock()
    service.register_function(
        "transition_to_next_node",
        AsyncMock(),
        is_node_transition=True,
    )

    await service._handle_tool_invocation(
        "transition_to_next_node", "call-transition", {"reason": "pricing"}
    )
    await service._handle_tool_invocation("lookup_price", "call-lookup", {})

    assert service._pending_node_transition_tool_call_ids == {"call-transition"}
    assert service.run_function_calls.await_count == 2


@pytest.mark.asyncio
async def test_node_transition_invocation_waits_for_response_end():
    service = _make_service()
    service.run_function_calls = AsyncMock()
    service.stop_processing_metrics = AsyncMock()
    service.push_frame = AsyncMock()
    service.register_function(
        "transition_to_next_node",
        AsyncMock(),
        is_node_transition=True,
    )
    service._bot_responding = "voice"

    await service._handle_tool_invocation(
        "transition_to_next_node", "call-transition", {"reason": "pricing"}
    )

    service.run_function_calls.assert_not_awaited()
    assert service._deferred_node_transition_tool_invocations == [
        (
            "transition_to_next_node",
            "call-transition",
            {"reason": "pricing"},
        )
    ]

    await service._handle_response_end()

    service.run_function_calls.assert_awaited_once()
    function_call = service.run_function_calls.await_args.args[0][0]
    assert function_call.function_name == "transition_to_next_node"
    assert function_call.tool_call_id == "call-transition"
    assert function_call.arguments == {"reason": "pricing"}
    assert service._deferred_node_transition_tool_invocations == []
    assert service._bot_responding is None


@pytest.mark.asyncio
async def test_ordinary_tool_invocation_runs_while_response_is_active():
    service = _make_service()
    service.run_function_calls = AsyncMock()
    service._bot_responding = "voice"

    await service._handle_tool_invocation("lookup_price", "call-lookup", {})

    service.run_function_calls.assert_awaited_once()
    assert service._deferred_node_transition_tool_invocations == []


def test_ultravox_requires_transition_context_aggregation():
    service = _make_service()

    assert service._requires_node_transition_context_aggregation() is True


@pytest.mark.asyncio
async def test_messages_append_frame_sends_user_text():
    service = _make_service()
    service._socket = object()
    service._call_started = True
    service._send_user_text = AsyncMock()

    await service._handle_messages_append(
        LLMMessagesAppendFrame(
            [{"role": "user", "content": "Are you still there?"}],
            run_llm=True,
        )
    )

    service._send_user_text.assert_awaited_once_with("Are you still there?")


@pytest.mark.asyncio
async def test_messages_append_frame_queues_user_text_until_call_started():
    service = _make_service()
    service._socket = object()
    service._call_started = False
    service._send_user_text = AsyncMock()

    await service._handle_messages_append(
        LLMMessagesAppendFrame(
            [{"role": "user", "content": "Are you still there?"}],
            run_llm=True,
        )
    )

    assert service._pending_user_text_messages == ["Are you still there?"]
    service._send_user_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_started_flushes_pending_user_text_messages():
    service = _make_service()
    service._pending_user_text_messages = [
        "First queued message",
        "Second queued message",
    ]
    service._send_user_text = AsyncMock()
    service._socket = _MessageSocket(['{"type":"call_started","callId":"call-123"}'])

    await service._receive_messages()

    assert service._call_started is True
    assert service._pending_user_text_messages == []
    assert service._send_user_text.await_args_list == [
        call("First queued message"),
        call("Second queued message"),
    ]


@pytest.mark.asyncio
async def test_completed_input_transcription_is_broadcast_as_finalized():
    service = _make_service()
    service.broadcast_frame = AsyncMock()
    service._last_user_id = "caller-1"

    await service._handle_user_transcript("Hello there")

    service.broadcast_frame.assert_awaited_once()
    assert service.broadcast_frame.await_args.args[0].__name__ == "TranscriptionFrame"
    assert service.broadcast_frame.await_args.kwargs["text"] == "Hello there"
    assert service.broadcast_frame.await_args.kwargs["finalized"] is True


def test_build_one_shot_params_uses_explicit_greeting_text():
    service = _make_service()

    params = service._build_one_shot_params(
        greeting_text="Welcome to Dograh",
        agent_speaks_first=True,
    )

    assert params.extra["firstSpeakerSettings"] == {
        "agent": {"text": "Welcome to Dograh"}
    }


def test_build_one_shot_params_uses_current_system_instruction():
    service = _make_service()
    service._settings.system_instruction = "Base instruction"

    params = service._build_one_shot_params(
        greeting_text=None,
        agent_speaks_first=True,
    )

    assert params.system_prompt == "Base instruction"


def test_to_selected_tools_includes_registered_timeout():
    service = _make_service()
    service.register_function(
        "transition_to_next_node",
        AsyncMock(),
        timeout_secs=5.5,
    )

    selected_tools = service._to_selected_tools(_tool_schema())

    assert selected_tools == [
        {
            "temporaryTool": {
                "modelToolName": "transition_to_next_node",
                "description": "Move to the next workflow node",
                "dynamicParameters": [
                    {
                        "name": "reason",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {"type": "string"},
                        "required": False,
                    }
                ],
                "client": {},
                "timeout": "5.5s",
            }
        }
    ]


@pytest.mark.asyncio
async def test_receive_messages_ignores_benign_websocket_close():
    service = _make_service()
    service._socket = _ClosingSocket(
        ConnectionClosedError(None, Close(1000, "OK"), None)
    )

    await service._receive_messages()

    service.push_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_receive_messages_reports_unexpected_websocket_close():
    service = _make_service()
    service._socket = _ClosingSocket(
        ConnectionClosedError(None, Close(1011, "internal error"), None)
    )

    await service._receive_messages()

    service.push_error.assert_awaited_once()


def test_factory_creates_dograh_ultravox_realtime_service():
    effective_config = EffectiveAIModelConfiguration(
        is_realtime=True,
        realtime=UltravoxRealtimeLLMConfiguration(
            provider="ultravox_realtime",
            api_key="ultra-key",
            model="ultravox-v0.7",
            voice="Mark",
        ),
    )

    service = create_realtime_llm_service(
        effective_config,
        audio_config=SimpleNamespace(),
    )

    assert isinstance(service, DograhUltravoxRealtimeLLMService)
    assert service._params.voice == "Mark"


def test_ultravox_realtime_configuration_defaults_to_mark_voice():
    config = UltravoxRealtimeLLMConfiguration(
        provider="ultravox_realtime",
        api_key="ultra-key",
        model="ultravox-v0.7",
    )

    assert config.voice == "Mark"
