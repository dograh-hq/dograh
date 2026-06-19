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
    _RESUMPTION_USER_MESSAGE,
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
    assert service._connect_call.await_args.kwargs["initial_messages"] is None
    assert service._connect_call.await_args.kwargs["agent_speaks_first"] is True


@pytest.mark.asyncio
async def test_system_instruction_update_marks_reconnect_required():
    service = _make_service()
    service._has_connected_once = True

    changed = await service._update_settings(
        DograhUltravoxRealtimeLLMService.Settings(system_instruction="new instruction")
    )

    assert "system_instruction" in changed
    assert service._reconnect_required is True


@pytest.mark.asyncio
async def test_system_instruction_change_reconnects_with_full_initial_messages():
    service = _make_service()
    service._socket = object()
    service._has_connected_once = True
    service._call_system_instruction = "old instruction"
    service._reconnect_required = True
    service._settings.system_instruction = "new instruction"
    service._reconnect_with_context = AsyncMock()

    context = LLMContext(
        messages=[
            {"role": "user", "content": "I want to hear the pricing."},
            {
                "role": "assistant",
                "content": "Let me check that for you.",
                "tool_calls": [
                    {
                        "id": "call-transition",
                        "type": "function",
                        "function": {
                            "name": "transition_to_next_node",
                            "arguments": '{"reason":"pricing requested"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-transition",
                "content": '{"status":"done"}',
            },
        ],
        tools=_tool_schema(),
    )

    await service._handle_context(context)

    service._reconnect_with_context.assert_awaited_once()
    initial_messages = service._reconnect_with_context.await_args.kwargs[
        "initial_messages"
    ]
    assert initial_messages == [
        {
            "role": "MESSAGE_ROLE_USER",
            "text": "I want to hear the pricing.",
        },
        {
            "role": "MESSAGE_ROLE_AGENT",
            "text": "Let me check that for you.",
        },
        {
            "role": "MESSAGE_ROLE_TOOL_CALL",
            "text": "",
            "invocationId": "call-transition",
            "toolName": "transition_to_next_node",
        },
        {
            "role": "MESSAGE_ROLE_TOOL_RESULT",
            "text": '{"status":"done"}',
            "invocationId": "call-transition",
            "toolName": "transition_to_next_node",
        },
    ]
    assert "call-transition" in service._completed_tool_calls


@pytest.mark.asyncio
async def test_tool_context_update_does_not_reconnect_when_system_instruction_is_unchanged():
    service = _make_service()
    service._socket = object()
    service._call_system_instruction = "same instruction"
    service._settings.system_instruction = "same instruction"
    service._reconnect_with_context = AsyncMock()
    service._send_tool_result = AsyncMock()

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

    service._reconnect_with_context.assert_not_awaited()
    service._send_tool_result.assert_awaited_once_with(
        "call-transition",
        '{"status":"done"}',
    )


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
        initial_messages=None,
        agent_speaks_first=True,
    )

    assert params.extra["firstSpeakerSettings"] == {
        "agent": {"text": "Welcome to Dograh"}
    }


def test_build_one_shot_params_includes_initial_messages():
    service = _make_service()
    service._settings.system_instruction = "Base instruction"

    params = service._build_one_shot_params(
        greeting_text=None,
        initial_messages=[
            {"role": "MESSAGE_ROLE_USER", "text": "User asked a question."},
            {"role": "MESSAGE_ROLE_TOOL_RESULT", "text": '{"status":"done"}'},
        ],
        agent_speaks_first=True,
    )

    assert params.extra["initialMessages"] == [
        {"role": "MESSAGE_ROLE_USER", "text": "User asked a question."},
        {"role": "MESSAGE_ROLE_TOOL_RESULT", "text": '{"status":"done"}'},
        {"role": "MESSAGE_ROLE_USER", "text": _RESUMPTION_USER_MESSAGE},
    ]
    assert params.system_prompt == "Base instruction"


def test_build_one_shot_params_without_tool_result_does_not_add_resumption_user_message():
    service = _make_service()
    service._settings.system_instruction = "Base instruction"

    params = service._build_one_shot_params(
        greeting_text=None,
        initial_messages=[
            {"role": "MESSAGE_ROLE_USER", "text": "User asked a question."},
            {"role": "MESSAGE_ROLE_AGENT", "text": "Assistant replied."},
        ],
        agent_speaks_first=False,
    )

    assert params.system_prompt == "Base instruction"


def test_should_agent_speak_first_when_history_ends_with_tool_result():
    service = _make_service()

    assert (
        service._should_agent_speak_first(
            [
                {"role": "MESSAGE_ROLE_USER", "text": "Hello"},
                {"role": "MESSAGE_ROLE_TOOL_RESULT", "text": '{"status":"done"}'},
            ]
        )
        is True
    )


def test_should_not_force_agent_speaks_first_when_history_ends_with_agent():
    service = _make_service()

    assert (
        service._should_agent_speak_first(
            [{"role": "MESSAGE_ROLE_AGENT", "text": "How else can I help?"}]
        )
        is False
    )


def test_should_add_resumption_user_message_only_when_history_ends_with_tool_result():
    service = _make_service()

    assert (
        service._should_add_resumption_user_message(
            [{"role": "MESSAGE_ROLE_TOOL_RESULT", "text": '{"status":"done"}'}]
        )
        is True
    )
    assert (
        service._should_add_resumption_user_message(
            [{"role": "MESSAGE_ROLE_AGENT", "text": "Assistant replied."}]
        )
        is False
    )


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
