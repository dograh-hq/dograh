import asyncio
import base64
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.adapters.services.aws_nova_sonic_adapter import Role
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    InputAudioRawFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.aws.nova_sonic.llm import AWSNovaSonicLLMService

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.masking import mask_user_config
from api.services.configuration.registry import (
    REALTIME_PROVIDERS,
    AWSNovaSonicRealtimeLLMConfiguration,
    ServiceProviders,
)
from api.services.integrations.paygent.collector import _is_sts_processor_name
from api.services.pipecat.realtime.aws_nova_sonic import (
    DograhAWSNovaSonicLLMService,
)
from api.services.pipecat.service_factory import create_realtime_llm_service


def _make_service() -> DograhAWSNovaSonicLLMService:
    return DograhAWSNovaSonicLLMService(
        secret_access_key="test-secret",
        access_key_id="test-access",
        session_token="test-session",
        region="us-east-1",
    )


def test_nova_2_sonic_is_registered_as_a_realtime_provider():
    config = AWSNovaSonicRealtimeLLMConfiguration(
        aws_access_key="access",
        aws_secret_key="secret",
    )

    assert ServiceProviders.AWS_NOVA_SONIC.value in REALTIME_PROVIDERS
    assert config.model == "amazon.nova-2-sonic-v1:0"
    assert config.voice == "matthew"
    assert config.aws_region == "us-east-1"
    assert config.temperature == 0.7
    assert config.max_tokens == 1024
    assert config.top_p == 0.9


def test_nova_credentials_are_validated_without_an_api_key():
    config = AWSNovaSonicRealtimeLLMConfiguration(
        aws_access_key="access",
        aws_secret_key="secret",
    )

    result = UserConfigurationValidator()._validate_service(config, "realtime")

    assert result == []


def test_nova_iam_credentials_and_session_token_are_masked():
    config = EffectiveAIModelConfiguration(
        is_realtime=True,
        realtime=AWSNovaSonicRealtimeLLMConfiguration(
            aws_access_key="AKIAEXAMPLE1234",
            aws_secret_key="secret-value-5678",
            aws_session_token="session-token-9012",
        ),
    )

    masked = mask_user_config(config)["realtime"]

    assert masked["aws_access_key"].endswith("1234")
    assert masked["aws_secret_key"].endswith("5678")
    assert masked["aws_session_token"].endswith("9012")
    assert "AKIAEXAMPLE" not in masked["aws_access_key"]
    assert "secret-value" not in masked["aws_secret_key"]
    assert "session-token" not in masked["aws_session_token"]


def test_factory_creates_nova_service_with_credentials_and_audio_config():
    effective_config = EffectiveAIModelConfiguration(
        is_realtime=True,
        realtime=AWSNovaSonicRealtimeLLMConfiguration(
            provider="aws_nova_sonic",
            aws_access_key="access",
            aws_secret_key="secret",
            aws_session_token="session",
            aws_region="us-west-2",
            voice="tiffany",
            endpointing_sensitivity="HIGH",
            temperature=0.5,
            max_tokens=2048,
            top_p=0.8,
        ),
    )

    service = create_realtime_llm_service(
        effective_config,
        audio_config=SimpleNamespace(
            transport_in_sample_rate=8000,
            transport_out_sample_rate=16000,
        ),
    )

    assert isinstance(service, DograhAWSNovaSonicLLMService)
    assert service._access_key_id == "access"
    assert service._secret_access_key == "secret"
    assert service._session_token == "session"
    assert service._region == "us-west-2"
    assert service._settings.model == "amazon.nova-2-sonic-v1:0"
    assert service._settings.voice == "tiffany"
    assert service._settings.endpointing_sensitivity == "HIGH"
    assert service._settings.temperature == 0.5
    assert service._settings.max_tokens == 2048
    assert service._settings.top_p == 0.8
    assert service.audio_config.input_sample_rate == 8000
    assert service.audio_config.output_sample_rate == 16000


def test_factory_normalizes_blank_nova_session_token():
    effective_config = EffectiveAIModelConfiguration(
        is_realtime=True,
        realtime=AWSNovaSonicRealtimeLLMConfiguration(
            aws_access_key="access",
            aws_secret_key="secret",
            aws_session_token="",
        ),
    )

    service = create_realtime_llm_service(
        effective_config,
        audio_config=SimpleNamespace(
            transport_in_sample_rate=16000,
            transport_out_sample_rate=24000,
        ),
    )

    assert service._session_token is None


@pytest.mark.parametrize(
    "messages,expected",
    [
        ([], []),
        ([{"role": "user", "content": "Hello"}], [(Role.USER, "Hello")]),
        (
            [
                {"role": "system", "content": "Call instructions"},
                {"role": "assistant", "content": "Welcome"},
                {"role": "user", "content": "Hello"},
            ],
            [(Role.USER, None), (Role.ASSISTANT, "Welcome"), (Role.USER, "Hello")],
        ),
        (
            [
                {"role": "assistant", "content": None, "tool_calls": []},
                {"role": "tool", "tool_call_id": "old-call", "content": "done"},
                {"role": "assistant", "content": "Welcome"},
                {"role": "assistant", "content": "How can I help?"},
                {"role": "user", "content": "Hello"},
                {"role": "user", "content": "I have a question"},
            ],
            [
                (Role.USER, None),
                (Role.ASSISTANT, "Welcome\nHow can I help?"),
                (Role.USER, "Hello\nI have a question"),
            ],
        ),
    ],
)
def test_nova_history_starts_with_user_and_alternates_without_changing_transcript(
    messages, expected
):
    service = _make_service()
    context = LLMContext(deepcopy(messages))

    # Repeated reconnects must not accumulate synthetic messages in the context.
    for _ in range(2):
        params = service.get_llm_adapter().get_llm_invocation_params(context)
        history = params["messages"]
        assert [m.role for m in history] == [role for role, _ in expected]
        for message, (_, text) in zip(history, expected):
            if text is None:
                assert message.text
            else:
                assert message.text == text
        assert context.messages == messages


@pytest.mark.asyncio
@pytest.mark.parametrize("reconnect_kind", ["node", "reset"])
async def test_reconnect_serializes_valid_history_and_tracks_existing_tool_results(
    reconnect_kind,
):
    service = _make_service()
    context = LLMContext(
        [
            {"role": "assistant", "content": "Welcome"},
            {"role": "user", "content": "Yes, please continue"},
            {"role": "assistant", "content": "Let me ask a few questions"},
            {"role": "tool", "tool_call_id": "old-call", "content": "done"},
        ]
    )
    original_messages = deepcopy(context.messages)
    service._context = context
    service._handled_initial_context = True
    service._send_prompt_start_event = AsyncMock()
    service._send_text_event = AsyncMock()
    service._send_tool_result = AsyncMock()
    service._sc.start_monitor = MagicMock()
    service._receive_task_handler = AsyncMock()

    def close_unused_receive_task(coro):
        coro.close()

    service.create_task = MagicMock(side_effect=close_unused_receive_task)

    async def start_audio():
        service._audio_input_started = True

    service._send_audio_input_start_event = AsyncMock(side_effect=start_audio)
    service._disconnect = AsyncMock()

    async def connect():
        service._ready_to_send_context = True
        await service._finish_connecting_if_context_available()

    service._start_connecting = AsyncMock(side_effect=connect)
    if reconnect_kind == "node":
        service._awaiting_node_transition_context = True
        await service._handle_context(context)
    else:
        await service.reset_conversation()

    calls = service._send_text_event.await_args_list
    history = [c for c in calls if not c.kwargs.get("interactive")]
    roles = [c.kwargs["role"] for c in history if c.kwargs["role"] != Role.SYSTEM]
    assert roles == [Role.USER, Role.ASSISTANT, Role.USER, Role.ASSISTANT]
    if reconnect_kind == "node":
        assert sum(bool(c.kwargs.get("interactive")) for c in calls) == 1
    assert "old-call" in service._completed_tool_calls
    service._send_tool_result.assert_not_awaited()
    assert service._context is context
    assert context.messages == original_messages


@pytest.mark.asyncio
async def test_initial_context_triggers_native_nova_response_when_prepopulated():
    service = _make_service()
    context = LLMContext()
    service._context = context
    service._connected_time = 1.0
    service._audio_input_started = True
    service._send_text_event = AsyncMock()
    service._process_completed_function_calls = AsyncMock()

    await service._handle_context(context)

    assert service._handled_initial_context is True
    assert service._context is context
    service._send_text_event.assert_awaited_once()
    assert service._send_text_event.await_args.args[1] is Role.USER
    assert service._send_text_event.await_args.kwargs["interactive"] is True
    service._process_completed_function_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_tts_greeting_sends_exact_static_greeting_prompt():
    service = _make_service()
    service._context = LLMContext()
    service._connected_time = 1.0
    service._audio_input_started = True
    service._send_text_event = AsyncMock()

    await service.process_frame(
        TTSSpeakFrame("Hi Sam, this is Sarah from Acme.", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    service._send_text_event.assert_awaited_once()
    prompt, role = service._send_text_event.await_args.args
    assert role is Role.USER
    assert "The phone call has just connected. Greet the caller now:" in prompt
    assert prompt.endswith('"Hi Sam, this is Sarah from Acme."')
    assert service._send_text_event.await_args.kwargs["interactive"] is True


@pytest.mark.asyncio
async def test_initial_greeting_waits_for_audio_input_to_start():
    service = _make_service()
    service._context = LLMContext()
    service._connected_time = None
    service._ready_to_send_context = False
    service._send_text_event = AsyncMock()

    await service.process_frame(
        TTSSpeakFrame("Welcome to Dograh", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    service._send_text_event.assert_not_awaited()
    assert service._pending_initial_prompt is not None

    service._audio_input_started = True
    await service._flush_pending_text_inputs()

    service._send_text_event.assert_awaited_once()
    assert service._pending_initial_prompt is None


@pytest.mark.asyncio
async def test_messages_append_frame_sends_interactive_user_text():
    service = _make_service()
    service._audio_input_started = True
    service._send_text_event = AsyncMock()

    await service._handle_messages_append(
        LLMMessagesAppendFrame(
            [{"role": "user", "content": "Are you still there?"}],
            run_llm=True,
        )
    )

    service._send_text_event.assert_awaited_once_with(
        "Are you still there?", Role.USER, interactive=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_rate", [8000, 16000])
async def test_muted_audio_keeps_nova_stream_alive_without_sending_user_speech(
    sample_rate,
):
    service = _make_service()
    service.push_frame = AsyncMock()
    service._sc.on_audio_input = MagicMock()
    service._send_user_audio_event = AsyncMock()
    audio = b"\x01\x02" * (sample_rate // 50)
    frame = InputAudioRawFrame(audio=audio, sample_rate=sample_rate, num_channels=1)

    await service.process_frame(UserMuteStartedFrame(), FrameDirection.DOWNSTREAM)
    await service._handle_input_audio_frame(frame)

    silence = bytes(len(audio))
    service._sc.on_audio_input.assert_called_once_with(silence)
    service._send_user_audio_event.assert_awaited_once_with(silence)
    # Other consumers, such as call recording, must retain the caller's audio.
    assert frame.audio == audio

    service._sc.on_audio_input.reset_mock()
    service._send_user_audio_event.reset_mock()
    await service.process_frame(UserMuteStoppedFrame(), FrameDirection.DOWNSTREAM)
    await service._handle_input_audio_frame(frame)

    service._sc.on_audio_input.assert_called_once_with(audio)
    service._send_user_audio_event.assert_awaited_once_with(audio)


@pytest.mark.asyncio
async def test_muted_audio_buffers_only_silence_during_session_handoff():
    service = _make_service()
    service._user_is_muted = True
    service._sc = SimpleNamespace(on_audio_input=MagicMock(), handoff_in_progress=True)
    service._send_user_audio_event = AsyncMock()
    frame = InputAudioRawFrame(
        audio=b"\x01\x02" * 320, sample_rate=16000, num_channels=1
    )

    await service._handle_input_audio_frame(frame)

    service._sc.on_audio_input.assert_called_once_with(bytes(640))
    service._send_user_audio_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("muted", [False, True])
async def test_node_reconnect_skips_audio_while_closing_and_resumes_on_new_stream(
    muted,
):
    service = _make_service()
    old_stream = SimpleNamespace(input_stream=SimpleNamespace(send=AsyncMock()))
    new_stream = SimpleNamespace(input_stream=SimpleNamespace(send=AsyncMock()))
    close_started = asyncio.Event()
    finish_close = asyncio.Event()

    async def close_stream():
        old_stream.input_stream.send.side_effect = OSError(
            "Attempted to write to closed stream."
        )
        close_started.set()
        await finish_close.wait()

    old_stream.close = close_stream
    service._stream = old_stream
    service._prompt_name = "old-prompt"
    service._input_audio_content_name = "old-audio"
    service._audio_input_started = True
    service._connected_time = 1.0
    service._handled_initial_context = True
    service._awaiting_node_transition_context = True
    service._user_is_muted = muted
    service.push_error = AsyncMock()

    async def connect():
        service._stream = new_stream
        service._prompt_name = "new-prompt"
        service._input_audio_content_name = "new-audio"
        service._audio_input_started = True
        service._connected_time = 2.0

    service._start_connecting = AsyncMock(side_effect=connect)
    context = LLMContext([{"role": "user", "content": "Yes, continue"}])
    frame = InputAudioRawFrame(
        audio=b"\x01\x02" * 320, sample_rate=16000, num_channels=1
    )
    reconnect = asyncio.create_task(service._handle_context(context))
    try:
        await asyncio.wait_for(close_started.wait(), timeout=2)
        await service._handle_input_audio_frame(frame)
        old_stream.input_stream.send.assert_not_awaited()
    finally:
        finish_close.set()
        await asyncio.wait_for(reconnect, timeout=3)

    await service._handle_input_audio_frame(frame)

    new_stream.input_stream.send.assert_awaited_once()
    event = new_stream.input_stream.send.await_args.args[0]
    audio_input = json.loads(event.value.bytes_)["event"]["audioInput"]
    assert audio_input["promptName"] == "new-prompt"
    assert audio_input["contentName"] == "new-audio"
    assert base64.b64decode(audio_input["content"]) == (
        bytes(len(frame.audio)) if muted else frame.audio
    )
    service.push_error.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_after_send", ["disconnecting", "disconnected", "reconnected"]
)
async def test_audio_send_ignores_in_flight_failure_after_disconnect(state_after_send):
    service = _make_service()
    send_started = asyncio.Event()
    finish_send = asyncio.Event()

    async def send(event):
        send_started.set()
        await finish_send.wait()
        raise OSError("Attempted to write to closed stream.")

    service._stream = SimpleNamespace(input_stream=SimpleNamespace(send=send))
    service._prompt_name = "old-prompt"
    service._input_audio_content_name = "old-audio"
    service._audio_input_started = True
    service.push_error = AsyncMock()
    sending = asyncio.create_task(service._send_user_audio_event(bytes(640)))
    try:
        await asyncio.wait_for(send_started.wait(), timeout=2)
        if state_after_send == "disconnecting":
            service._disconnecting = True
        elif state_after_send == "disconnected":
            service._stream = None
        else:
            service._stream = SimpleNamespace(
                input_stream=SimpleNamespace(send=AsyncMock())
            )
    finally:
        finish_send.set()
        await asyncio.wait_for(sending, timeout=2)

    service.push_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_audio_send_reports_failure_on_active_stream():
    service = _make_service()
    error = OSError("Failed to write to stream.")
    service._stream = SimpleNamespace(
        input_stream=SimpleNamespace(send=AsyncMock(side_effect=error))
    )
    service._prompt_name = "active-prompt"
    service._input_audio_content_name = "active-audio"
    service._audio_input_started = True
    service.push_error = AsyncMock()

    await service._send_user_audio_event(bytes(640))

    service.push_error.assert_awaited_once()
    assert service.push_error.await_args.kwargs["exception"] is error
    assert str(error) in service.push_error.await_args.kwargs["error_msg"]


@pytest.mark.asyncio
async def test_completed_nova_transcription_is_marked_final(monkeypatch):
    service = _make_service()
    upstream_push = AsyncMock()
    monkeypatch.setattr(AWSNovaSonicLLMService, "push_frame", upstream_push)
    frame = TranscriptionFrame(text="Hello there", user_id="caller", timestamp="")

    await service.push_frame(frame, FrameDirection.UPSTREAM)

    assert frame.finalized is True
    upstream_push.assert_awaited_once_with(frame, FrameDirection.UPSTREAM)


@pytest.mark.asyncio
async def test_node_transition_tool_waits_for_nova_audio_turn(monkeypatch):
    service = _make_service()
    service._context = LLMContext()
    service._assistant_is_responding = True
    service.register_function(
        "transition_to_next_node",
        AsyncMock(),
        is_node_transition=True,
    )
    upstream_run = AsyncMock()
    monkeypatch.setattr(AWSNovaSonicLLMService, "run_function_calls", upstream_run)
    function_call = FunctionCallFromLLM(
        context=service._context,
        tool_call_id="call-1",
        function_name="transition_to_next_node",
        arguments={"reason": "done"},
    )

    await service.run_function_calls([function_call])

    upstream_run.assert_not_awaited()
    assert service._deferred_node_transition_function_calls == [function_call]

    service._assistant_is_responding = False
    await service._run_deferred_node_transition_function_calls()

    upstream_run.assert_awaited_once_with([function_call])
    assert service._deferred_node_transition_function_calls == []


@pytest.mark.asyncio
async def test_ordinary_tool_runs_while_nova_audio_turn_is_active(monkeypatch):
    service = _make_service()
    service._context = LLMContext()
    service._assistant_is_responding = True
    upstream_run = AsyncMock()
    monkeypatch.setattr(AWSNovaSonicLLMService, "run_function_calls", upstream_run)
    function_call = FunctionCallFromLLM(
        context=service._context,
        tool_call_id="call-1",
        function_name="lookup_customer",
        arguments={},
    )

    await service.run_function_calls([function_call])

    upstream_run.assert_awaited_once_with([function_call])
    assert service._deferred_node_transition_function_calls == []


@pytest.mark.asyncio
async def test_node_prompt_update_reconnects_after_updated_context_arrives():
    service = _make_service()
    service._handled_initial_context = True
    service._connected_time = 1.0

    await service._update_settings(
        service.Settings(system_instruction="You are the next workflow node.")
    )

    assert service._awaiting_node_transition_context is True

    service._disconnect = AsyncMock()

    async def mark_connected():
        service._connected_time = 2.0

    service._start_connecting = AsyncMock(side_effect=mark_connected)
    service._sc._conversation_history = [{"role": "USER", "text": "old"}]
    updated_context = LLMContext([{"role": "user", "content": "Current turn"}])

    await service._handle_context(updated_context)

    service._disconnect.assert_awaited_once()
    service._start_connecting.assert_awaited_once()
    assert service._context is updated_context
    assert service._awaiting_node_transition_context is False
    assert service._sc._conversation_history == []
    assert service._pending_initial_prompt is None


@pytest.mark.asyncio
async def test_node_reconnect_triggers_response_when_history_ends_with_assistant():
    service = _make_service()
    service._awaiting_node_transition_context = True
    service._disconnect = AsyncMock()
    service._send_text_event = AsyncMock()

    async def mark_connected_and_flush():
        service._connected_time = 2.0
        service._audio_input_started = True
        await service._flush_pending_text_inputs()

    service._start_connecting = AsyncMock(side_effect=mark_connected_and_flush)
    updated_context = LLMContext(
        [{"role": "assistant", "content": "I will transfer you now."}]
    )

    await service._handle_context(updated_context)

    service._send_text_event.assert_awaited_once_with(
        "Continue the conversation now, following your current instructions.",
        Role.USER,
        interactive=True,
    )
    assert service._pending_initial_prompt is None


def test_nova_requires_transition_context_aggregation():
    assert _make_service()._requires_node_transition_context_aggregation() is True


def test_nova_processor_name_is_recognized_as_speech_to_speech_usage():
    processor_name = _make_service().name.lower()

    assert "novasonic" in processor_name
    assert "realtime" not in processor_name
    assert "live" not in processor_name
    assert _is_sts_processor_name(processor_name) is True
