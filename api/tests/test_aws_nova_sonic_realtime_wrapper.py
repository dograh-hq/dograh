from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.adapters.services.aws_nova_sonic_adapter import Role
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
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
async def test_muted_audio_is_not_forwarded_to_nova():
    service = _make_service()
    service._sc.on_audio_input = MagicMock()
    service._send_user_audio_event = AsyncMock()
    frame = SimpleNamespace(audio=b"pcm")

    service._user_is_muted = True
    await service._handle_input_audio_frame(frame)

    service._sc.on_audio_input.assert_not_called()
    service._send_user_audio_event.assert_not_awaited()

    service._user_is_muted = False
    await service._handle_input_audio_frame(frame)

    service._sc.on_audio_input.assert_called_once_with(b"pcm")
    service._send_user_audio_event.assert_awaited_once_with(b"pcm")


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
