"""Exercise shared conversation policy through the real provider adapters."""

import base64
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.aws.nova_sonic.llm import Role
from pipecat.services.google.gemini_live.vertex.llm import GeminiLiveVertexLLMService

from api.services.pipecat.realtime.aws_nova_sonic import DograhAWSNovaSonicLLMService
from api.services.pipecat.realtime.azure_realtime import DograhAzureRealtimeLLMService
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.realtime.gemini_live_vertex import (
    DograhGeminiLiveVertexLLMService,
)
from api.services.pipecat.realtime.grok_realtime import DograhGrokRealtimeLLMService
from api.services.pipecat.realtime.openai_live import DograhOpenAILiveLLMService
from api.services.pipecat.realtime.openai_realtime import DograhOpenAIRealtimeLLMService
from api.services.pipecat.realtime.ultravox_realtime import (
    DograhUltravoxOneShotInputParams,
    DograhUltravoxRealtimeLLMService,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager


@pytest.fixture(
    params=["openai", "azure", "live", "gemini", "vertex", "grok", "nova", "ultravox"]
)
def realtime_service(request, monkeypatch):
    provider = request.param
    if provider in {"gemini", "vertex"}:
        monkeypatch.setattr(
            DograhGeminiLiveLLMService, "create_client", lambda self: None
        )
        monkeypatch.setattr(
            GeminiLiveVertexLLMService, "_get_credentials", lambda *args: None
        )
        if provider == "vertex":
            service = DograhGeminiLiveVertexLLMService(
                project_id="test", location="us-central1"
            )
            assert service._get_history_config() is None
            assert service._project_id == "test"
        else:
            service = DograhGeminiLiveLLMService(api_key="test")
    elif provider == "nova":
        service = DograhAWSNovaSonicLLMService(
            secret_access_key="test", access_key_id="test", region="us-east-1"
        )
    elif provider == "ultravox":
        service = DograhUltravoxRealtimeLLMService(
            params=DograhUltravoxOneShotInputParams(api_key="test")
        )
    elif provider == "live":
        service = DograhOpenAILiveLLMService(
            api_key="test", backend_model="gpt-5.4-mini"
        )
    elif provider == "azure":
        service = DograhAzureRealtimeLLMService(
            api_key="test", base_url="wss://example.test/openai/v1/realtime"
        )
    elif provider == "grok":
        service = DograhGrokRealtimeLLMService(api_key="test")
    else:
        service = DograhOpenAIRealtimeLLMService(api_key="test")
    service.push_frame = AsyncMock()
    service.send_client_event = AsyncMock()
    return service


def audio_sender(service):
    """Replace only the network boundary, retaining upstream audio handling."""
    if isinstance(service, DograhGeminiLiveLLMService):
        service._session = SimpleNamespace(send_realtime_input=AsyncMock())
        service._ready_for_realtime_input = True
        service._vad_disabled = False
        sent = service._session.send_realtime_input
        return (
            service._send_user_audio,
            sent,
            lambda call: call.kwargs["audio"].data,
            16000,
        )
    if isinstance(service, DograhAWSNovaSonicLLMService):
        service._send_user_audio_event = AsyncMock()
        return (
            service._handle_input_audio_frame,
            service._send_user_audio_event,
            lambda call: call.args[0],
            16000,
        )
    if isinstance(service, DograhUltravoxRealtimeLLMService):
        service._socket = SimpleNamespace()
        service._send = AsyncMock()
        return (
            service._send_user_audio,
            service._send,
            lambda call: call.args[0],
            service._sample_rate,
        )
    if isinstance(service, DograhOpenAILiveLLMService):
        service._session_started = True
    else:
        service._api_session_ready = True
    rate = 16000 if isinstance(service, DograhGrokRealtimeLLMService) else 24000
    return (
        service._send_user_audio,
        service.send_client_event,
        lambda call: base64.b64decode(call.args[0].audio),
        rate,
    )


@pytest.mark.asyncio
async def test_muted_audio_keeps_duration_and_never_modifies_recording(
    realtime_service,
):
    service = realtime_service
    send, output, decode, wire_rate = audio_sender(service)
    frame = InputAudioRawFrame(b"\x01\x02" * 1600, 16000, 1)
    # Prime streaming filters with real speech before muting.
    for _ in range(3):
        await send(frame)
    output.reset_mock()
    await service.process_frame(UserMuteStartedFrame(), FrameDirection.DOWNSTREAM)
    for _ in range(10):
        await send(frame)
    muted = b"".join(decode(call) for call in output.await_args_list)
    assert 0.9 <= len(muted) / (wire_rate * 2) <= 1.1
    assert not any(muted)
    assert frame.audio == b"\x01\x02" * 1600
    assert frame.sample_rate == 16000

    output.reset_mock()
    await service.process_frame(UserMuteStoppedFrame(), FrameDirection.DOWNSTREAM)
    # Muted speech must not leak out of the resampler after unmuting either.
    quiet = InputAudioRawFrame(bytes(3200), 16000, 1)
    for _ in range(3):
        await send(quiet)
    # SOXR's int16 conversion adds dither of at most one sample unit.
    quiet_output = b"".join(decode(call) for call in output.await_args_list)
    assert (
        max(
            (abs(sample[0]) for sample in struct.iter_unpack("<h", quiet_output)),
            default=0,
        )
        <= 1
    )
    output.reset_mock()
    for _ in range(3):
        await send(frame)
    assert any(b"".join(decode(call) for call in output.await_args_list))


@pytest.mark.asyncio
async def test_only_opening_text_is_accepted_even_while_connecting(realtime_service):
    service = realtime_service
    service._context = LLMContext()
    if isinstance(service, DograhUltravoxRealtimeLLMService):
        service._connect_call = AsyncMock()
    if isinstance(service, DograhAWSNovaSonicLLMService):
        service._finish_connecting_if_context_available = AsyncMock()
    greeting = AsyncMock(wraps=service._handle_initial_greeting)
    service._handle_initial_greeting = greeting

    await service.process_frame(TTSSpeakFrame("Hello!"), FrameDirection.DOWNSTREAM)
    assert service._handled_initial_context
    await service.process_frame(
        TTSSpeakFrame("Let me check that."), FrameDirection.DOWNSTREAM
    )

    greeting.assert_awaited_once_with(service._context, "Hello!")
    assert not any(
        isinstance(call.args[0], TTSSpeakFrame)
        for call in service.push_frame.await_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("is_realtime", [True, False])
async def test_edge_speech_policy_preserves_node_transition(
    is_realtime, three_node_workflow_no_variable_extraction
):
    engine = PipecatEngine(
        llm=MagicMock(),
        context=LLMContext(),
        task=SimpleNamespace(queue_frame=AsyncMock()),
        workflow=three_node_workflow_no_variable_extraction,
        call_context_vars={"customer_name": "Test"},
        is_realtime=is_realtime,
    )
    # Keep the engine's transition orchestration real without opening sockets.
    engine.set_node = AsyncMock()
    transition = await engine._create_transition_func(
        "collect_info", "agent", transition_speech="Let me ask a few questions."
    )
    result = AsyncMock()
    await transition(SimpleNamespace(arguments={}, result_callback=result))

    if is_realtime:
        engine.call_worker.queue_frame.assert_not_awaited()
    else:
        frame = engine.call_worker.queue_frame.await_args.args[0]
        assert frame.text == "Let me ask a few questions."
        assert not frame.append_to_context
    assert await engine.should_mute_user(InputAudioRawFrame(bytes(640), 16000, 1)) == (
        not is_realtime
    )
    engine.set_node.assert_awaited_once_with(
        "agent", origin_visit_id=engine.active_agent.visit_id
    )
    assert result.await_args.args == ({"status": "done"},)


@pytest.mark.asyncio
@pytest.mark.parametrize("is_realtime", [True, False])
async def test_tool_message_reports_playback_only_when_text_is_queued(
    is_realtime, three_node_workflow_no_variable_extraction
):
    engine = PipecatEngine(
        task=SimpleNamespace(queue_frame=AsyncMock()),
        workflow=three_node_workflow_no_variable_extraction,
        call_context_vars={},
        is_realtime=is_realtime,
    )
    manager = CustomToolManager(engine)
    queued = await manager._play_config_message(
        {"messageType": "custom", "customMessage": "Goodbye!"}, append_to_context=True
    )
    assert queued == (not is_realtime)
    if is_realtime:
        engine.call_worker.queue_frame.assert_not_awaited()
    else:
        frame = engine.call_worker.queue_frame.await_args.args[0]
        assert frame.text == "Goodbye!"
        assert frame.append_to_context

    await engine.queue_text_message("Checking your account.", mute_user=True)
    audio = InputAudioRawFrame(bytes(640), 16000, 1)
    assert await engine.should_mute_user(audio) == (not is_realtime)
    if not is_realtime:
        assert await engine.should_mute_user(BotStartedSpeakingFrame())
        assert not await engine.should_mute_user(BotStoppedSpeakingFrame())


@pytest.mark.asyncio
async def test_nova_recorded_greeting_never_asks_for_a_turn():
    """Upstream replays a trailing user message with interactive=True.

    That is Nova's "generate now", and the context does end on a user turn
    whenever the answer supervisor releases after the callee has spoken. The
    open has to swallow it, or the model talks over the recording.
    """
    service = DograhAWSNovaSonicLLMService(
        secret_access_key="test", access_key_id="test", region="us-east-1"
    )
    context = LLMContext()
    context.add_message({"role": "user", "content": "Hello?"})
    service._context = context
    service._audio_input_started = True
    service._stream = SimpleNamespace()
    service._prompt_name = "bench"
    sent = []
    service.send_text = AsyncMock(
        side_effect=lambda text, role, prompt, stream, interactive: sent.append(
            (role, text, interactive)
        )
    )

    async def connect():
        # Stand in for upstream's connect: it replays the trailing user turn
        # as interactive, which is precisely what must not survive.
        await service._send_text_event("Hello?", Role.USER, interactive=True)

    service._finish_connecting_if_context_available = AsyncMock(side_effect=connect)

    await service.handle_prerecorded_greeting(context, GREETING)

    assert not any(interactive for _, _, interactive in sent), sent
    # The conversation already supplied a user turn for the greeting to answer,
    # so padding another would put two USER turns back to back.
    assert [role for role, _, _ in sent] == [Role.USER.value, Role.ASSISTANT.value]
    assert sent[-1][1] == GREETING


@pytest.mark.asyncio
async def test_gemini_mute_preserves_readiness_and_manual_activity_windows(monkeypatch):
    monkeypatch.setattr(DograhGeminiLiveLLMService, "create_client", lambda self: None)
    service = DograhGeminiLiveLLMService(api_key="test")
    send, output, decode, _ = audio_sender(service)
    service._user_is_muted = True
    frame = InputAudioRawFrame(b"\x01\x02" * 320, 16000, 1)

    service._ready_for_realtime_input = False
    await send(frame)
    output.assert_not_awaited()
    service._ready_for_realtime_input = True
    service._vad_disabled = True
    service._user_is_speaking = False
    await send(frame)
    output.assert_not_awaited()
    assert service._user_audio_preroll_buffer == bytes(640)

    service._user_is_speaking = True
    await send(frame)
    assert decode(output.await_args) == bytes(640)
    output.reset_mock()
    service._disconnecting = True
    await send(frame)
    output.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", ["key", "token"])
async def test_azure_shared_wrapper_retains_native_authentication(auth, monkeypatch):
    token_provider = AsyncMock(return_value="test-bearer-token")
    credentials = (
        {"api_key": "test-key"} if auth == "key" else {"token_provider": token_provider}
    )
    service = DograhAzureRealtimeLLMService(
        base_url="wss://example.test/openai/v1/realtime?model=my-deployment",
        **credentials,
    )
    connect = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(
        "pipecat.services.azure.realtime.llm.websocket_connect", connect
    )
    service._receive_task_handler = MagicMock(return_value=object())
    service.create_task = MagicMock()
    await service._connect()

    expected = (
        {"api-key": "test-key"}
        if auth == "key"
        else {"Authorization": "Bearer test-bearer-token"}
    )
    connect.assert_awaited_once_with(uri=service.base_url, additional_headers=expected)
    assert service.base_url.endswith("?model=my-deployment")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "azure", "grok"])
async def test_prompt_update_keeps_existing_realtime_connection(provider):
    cls = {
        "openai": DograhOpenAIRealtimeLLMService,
        "azure": DograhAzureRealtimeLLMService,
        "grok": DograhGrokRealtimeLLMService,
    }[provider]
    kwargs = (
        {"base_url": "wss://example.test/openai/v1/realtime"}
        if provider == "azure"
        else {}
    )
    service = cls(api_key="test", **kwargs)
    socket = SimpleNamespace()
    service._websocket = socket
    service._context = LLMContext()
    service._handled_initial_context = True
    service.send_client_event = AsyncMock()
    service._connect = AsyncMock()
    service._disconnect = AsyncMock()

    await service._update_settings(
        service.Settings(system_instruction="Ask for the address.")
    )

    updates = [
        call.args[0]
        for call in service.send_client_event.await_args_list
        if call.args[0].type == "session.update"
    ]
    assert len(updates) == 1
    assert updates[0].session.instructions == "Ask for the address."
    assert service._websocket is socket
    service._connect.assert_not_awaited()
    service._disconnect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoding,silence", [("audio/pcmu", 0xFF), ("audio/pcma", 0xD5)]
)
async def test_openai_encoded_audio_uses_codec_silence(encoding, silence):
    from pipecat.services.openai.realtime import events

    service = DograhOpenAIRealtimeLLMService(
        api_key="test",
        settings=DograhOpenAIRealtimeLLMService.Settings(
            session_properties=events.SessionProperties(
                audio={"input": {"format": {"type": encoding}}}
            )
        ),
    )
    service._user_is_muted = True
    service.send_client_event = AsyncMock()
    frame = InputAudioRawFrame(b"\x01" * 160, 8000, 1)
    await service._send_user_audio(frame)
    event = service.send_client_event.await_args.args[0]
    assert base64.b64decode(event.audio) == bytes([silence]) * 160
    assert frame.audio == b"\x01" * 160


GREETING = "Hi, this is Sarah."


def prerecorded_greeting_probe(service):
    """Open each provider at the point a recorded greeting reaches it.

    Returns a callable run after ``handle_prerecorded_greeting`` that checks
    the provider seeded the spoken greeting without requesting a turn.
    """
    if isinstance(service, DograhGeminiLiveLLMService):
        service._session = SimpleNamespace(
            send_client_content=AsyncMock(), send_realtime_input=AsyncMock()
        )

        def check():
            send = service._session.send_client_content
            assert send.await_args.kwargs["turn_complete"] is False
            spoken = [
                part.text
                for turn in send.await_args.kwargs["turns"]
                if turn.role == "model"
                for part in turn.parts
            ]
            assert spoken == [GREETING]
            # The " " nudge is the only thing that makes Gemini 3 generate.
            service._session.send_realtime_input.assert_not_awaited()
            # Without this the service discards every caller audio frame.
            assert service._ready_for_realtime_input is True

        return check

    if isinstance(service, DograhUltravoxRealtimeLLMService):
        service._connect_call = AsyncMock()

        def check():
            # A one-shot call has no history channel; waiting is all it can do.
            service._connect_call.assert_awaited_once_with(
                greeting_text=None, agent_speaks_first=False
            )

        return check

    if isinstance(service, DograhAWSNovaSonicLLMService):
        # Replace only the wire, not _send_text_event: the interactive
        # suppression that keeps Nova silent lives in that method, and mocking
        # it would step over the thing under test. Upstream's connect path is
        # still stubbed -- it opens a real AWS stream.
        service._finish_connecting_if_context_available = AsyncMock()
        service._audio_input_started = True
        service._stream = SimpleNamespace()
        service._prompt_name = "bench"
        sent = []
        service.send_text = AsyncMock(
            side_effect=lambda text, role, prompt, stream, interactive: sent.append(
                (role, text, interactive)
            )
        )

        def check():
            service._finish_connecting_if_context_available.assert_awaited_once()
            assert service._pending_initial_prompt is None
            # AWS rejects history that opens on ASSISTANT.
            assert [role for role, _, _ in sent] == [
                Role.USER.value,
                Role.ASSISTANT.value,
            ]
            assert sent[-1][1] == GREETING
            # `interactive=True` is Nova's "generate now"; none may be set.
            assert not any(interactive for _, _, interactive in sent)
            assert service._opening_after_prerecorded_greeting is False

        return check

    if isinstance(service, DograhOpenAILiveLLMService):
        service._session_started = True
        service.send_client_event = AsyncMock()

        def check():
            assert service._initial_backend_request is False
            appended = [
                call.args[0]
                for call in service.send_client_event.await_args_list
                if type(call.args[0]).__name__ == "SessionThinkingAppendEvent"
            ]
            assert appended and GREETING in appended[0].content
            # Commentary is the channel it paraphrases aloud.
            assert not any(
                type(call.args[0]).__name__ == "SessionCommentaryAppendEvent"
                for call in service.send_client_event.await_args_list
            )

        return check

    service._api_session_ready = True

    def check():
        sent = [call.args[0] for call in service.send_client_event.await_args_list]
        assert not any(event.type == "response.create" for event in sent), (
            f"{service} asked for a response after a recorded greeting"
        )
        assert service._llm_needs_conversation_setup is False
        items = [
            event.item for event in sent if event.type == "conversation.item.create"
        ]
        # Sent as its own assistant item: the adapter would otherwise repack it
        # as a "previously saved conversation" user turn.
        assert [(item.role, item.content[0].text) for item in items] == [
            ("assistant", GREETING)
        ]
        assert items[0].content[0].type == "output_text"

    return check


@pytest.mark.asyncio
async def test_recorded_greeting_opens_the_session_without_speaking(realtime_service):
    """A greeting played to the transport still has to open the session.

    Nothing else does: the recording bypasses the service entirely, so
    without this the provider is never seeded and Gemini Live drops every
    caller frame.
    """
    service = realtime_service
    context = LLMContext()
    service._context = context
    check = prerecorded_greeting_probe(service)

    await service.handle_prerecorded_greeting(context, GREETING)

    assert service._handled_initial_context
    check()
    # The aggregator owns the context write, once playback drains.
    assert context.get_messages() == []

    # The opening turn is spent: a later TTSSpeakFrame must not re-greet.
    await service.process_frame(TTSSpeakFrame("Hello!"), FrameDirection.DOWNSTREAM)
    assert not any(
        isinstance(call.args[0], TTSSpeakFrame)
        for call in service.push_frame.await_args_list
    )
