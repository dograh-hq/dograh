"""Unit tests for DograhGeminiLiveTranslateLLMService.

Covers the manual WebSocket implementation in
``api/services/pipecat/realtime/gemini_live_translate.py``:
  * setup-message wire format
  * unsupported-feature raise sites (process_frame + defense-in-depth)
  * server-message dispatch (modelTurn / outputTranscription / turn_complete
    / interrupted / toolCall / goAway)
  * client-side audio send wire format
  * reconnect failure counter

WebSocket I/O is mocked; we never open a real socket. Frame pushes are
captured via :pymeth:`FrameProcessor.push_frame` patches.
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pipecat.frames.frames import (
    AggregationType,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMSetToolChoiceFrame,
    LLMSetToolsFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.exceptions import UnsupportedRealtimeFeatureError
from api.services.pipecat.realtime.gemini_live_translate import (
    MAX_CONSECUTIVE_FAILURES,
    DograhGeminiLiveTranslateLLMService,
)


def _make_service(
    target_language_code: str = "es",
) -> DograhGeminiLiveTranslateLLMService:
    service = DograhGeminiLiveTranslateLLMService(
        api_key="test-key",
        settings=DograhGeminiLiveTranslateLLMService.Settings(
            target_language_code=target_language_code,
        ),
    )
    service.push_frame = AsyncMock()
    service.broadcast_interruption = AsyncMock()
    service.push_error = AsyncMock()
    return service


def test_build_setup_message_wire_format():
    service = _make_service(target_language_code="pl")
    setup = service._build_setup_message()
    # Wire format mirrors the google-genai>=2.8 SDK output exactly
    # (verified by serializing LiveConnectConfig through
    # _live_converters._LiveConnectParameters_to_mldev). The shape is
    # mixed-case: top-level setup keys and direct generationConfig
    # children (responseModalities, speechConfig, translationConfig)
    # are camelCase, but everything nested inside speechConfig and
    # translationConfig is snake_case.
    assert setup == {
        "setup": {
            "model": "models/gemini-3.5-live-translate-preview",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "translationConfig": {"target_language_code": "pl"},
                "speechConfig": {
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": "Puck"},
                    },
                },
            },
            "outputAudioTranscription": {},
            "inputAudioTranscription": {},
        }
    }
    # outputAudioTranscription must NOT live inside generationConfig — the
    # server rejects the whole setup with a 1007 close frame if it does.
    assert "outputAudioTranscription" not in setup["setup"]["generationConfig"]
    assert "inputAudioTranscription" not in setup["setup"]["generationConfig"]
    # speechConfig must be present — empirically the server stops emitting
    # any modelTurn audio when it is absent on the translate-preview model.
    assert "speechConfig" in setup["setup"]["generationConfig"]


def test_build_setup_message_includes_echo_target_language_when_enabled():
    service = DograhGeminiLiveTranslateLLMService(
        api_key="test-key",
        settings=DograhGeminiLiveTranslateLLMService.Settings(
            target_language_code="pl",
            echo_target_language=True,
        ),
    )
    setup = service._build_setup_message()
    translation_config = setup["setup"]["generationConfig"]["translationConfig"]
    assert translation_config == {
        "target_language_code": "pl",
        "echo_target_language": True,
    }


def test_build_setup_message_omits_echo_when_default_false():
    service = _make_service(target_language_code="pl")
    setup = service._build_setup_message()
    translation_config = setup["setup"]["generationConfig"]["translationConfig"]
    assert "echo_target_language" not in translation_config


def test_settings_validate_complete_is_noop():
    """``AIService.start`` calls ``self._settings.validate_complete()``.

    Regression: a missing ``validate_complete`` raised
    ``AttributeError`` on ``StartFrame``, which aborted pipeline startup
    and left the service silent.
    """
    settings = DograhGeminiLiveTranslateLLMService.Settings()
    assert settings.validate_complete() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame",
    [
        UserStartedSpeakingFrame(),
        BotStoppedSpeakingFrame(),
    ],
)
async def test_process_frame_forwards_unhandled_frames(frame):
    """Regression: unhandled frames must be pushed downstream.

    Without the catch-all ``else: push_frame``, every frame that this
    service does not explicitly handle (``StartFrame``, ``EndFrame``,
    speaking-state frames, etc.) is swallowed, and downstream processors
    never transition out of "not started" state — producing a flood of
    ``Trying to process InputAudioRawFrame ... but StartFrame not received yet``
    errors and a silent agent.

    ``StartFrame`` / ``EndFrame`` are not parametrized here because the
    base class lifecycle hooks they trigger require a full pipeline
    (TaskManager). The catch-all forwarding branch is the same regardless
    of frame type.
    """
    service = _make_service()
    await service.process_frame(frame, FrameDirection.DOWNSTREAM)
    service.push_frame.assert_any_await(frame, FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame",
    [
        LLMSetToolsFrame(tools=None),
        LLMSetToolChoiceFrame(tool_choice="auto"),
        LLMUpdateSettingsFrame(settings={"model": "other"}),
        LLMMessagesAppendFrame(messages=[{"role": "user", "content": "hi"}]),
    ],
)
async def test_process_frame_rejects_unsupported_features(frame):
    service = _make_service()
    with pytest.raises(UnsupportedRealtimeFeatureError):
        await service.process_frame(frame, FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_update_settings_always_raises():
    service = _make_service()
    with pytest.raises(UnsupportedRealtimeFeatureError):
        await service._update_settings(MagicMock())


@pytest.mark.asyncio
async def test_run_or_defer_function_calls_raises():
    service = _make_service()
    with pytest.raises(UnsupportedRealtimeFeatureError):
        await service._run_or_defer_function_calls([MagicMock()])


@pytest.mark.asyncio
async def test_handle_context_rejects_system_messages():
    service = _make_service()
    ctx = LLMContext(messages=[{"role": "system", "content": "be polite"}])
    with pytest.raises(UnsupportedRealtimeFeatureError):
        await service._handle_context(ctx)


@pytest.mark.asyncio
async def test_handle_context_drops_non_system_messages():
    service = _make_service()
    ctx = LLMContext(messages=[{"role": "user", "content": "hola"}])
    await service._handle_context(ctx)  # should not raise


@pytest.mark.asyncio
async def test_handle_msg_model_turn_emits_tts_audio_bracketed():
    service = _make_service()
    audio_bytes = b"\x01\x02\x03\x04"
    model_turn = {
        "parts": [
            {
                "inlineData": {
                    "mimeType": "audio/pcm;rate=24000",
                    "data": base64.b64encode(audio_bytes).decode("ascii"),
                }
            }
        ]
    }
    await service._handle_msg_model_turn(model_turn)

    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    assert isinstance(pushed[0], TTSStartedFrame)
    assert isinstance(pushed[1], LLMFullResponseStartFrame)
    assert isinstance(pushed[2], TTSAudioRawFrame)
    assert pushed[2].audio == audio_bytes
    assert pushed[2].sample_rate == 24000
    assert pushed[2].num_channels == 1
    assert service._bot_is_responding is True


@pytest.mark.asyncio
async def test_handle_msg_model_turn_skips_unknown_mime_type():
    service = _make_service()
    model_turn = {
        "parts": [
            {"inlineData": {"mimeType": "video/h264", "data": "AAAA"}},
        ]
    }
    await service._handle_msg_model_turn(model_turn)
    service.push_frame.assert_not_awaited()
    assert service._bot_is_responding is False


@pytest.mark.asyncio
async def test_handle_msg_output_transcription_emits_bot_text_frames():
    """Bot-translated text must flow as LLMTextFrame + TTSTextFrame.

    The dograh observer (``api/services/pipecat/realtime_feedback_observer.py``)
    treats every ``TranscriptionFrame`` as a user message regardless of
    ``user_id``, so the translated bot output must instead use the same
    frame types as pipecat's standard GeminiLive
    (``gemini_live/llm.py:_push_output_transcription_text_frames``): an
    ``LLMTextFrame`` (consumed by RTVI, marked ``append_to_context=False``)
    plus a sentence-aggregated ``TTSTextFrame`` that the
    BaseOutputTransport replays as bot text with audio-clock timing.
    """
    service = _make_service()
    await service._handle_msg_output_transcription({"text": "Hola mundo"})

    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    # Vertex-parity bracket: TTSStartedFrame + LLMFullResponseStartFrame first.
    assert isinstance(pushed[0], TTSStartedFrame)
    assert isinstance(pushed[1], LLMFullResponseStartFrame)
    llm_text = pushed[2]
    assert isinstance(llm_text, LLMTextFrame)
    assert llm_text.text == "Hola mundo"
    assert llm_text.append_to_context is False
    tts_text = pushed[3]
    assert isinstance(tts_text, TTSTextFrame)
    assert tts_text.text == "Hola mundo"
    assert tts_text.aggregated_by == AggregationType.SENTENCE
    assert tts_text.includes_inter_frame_spaces is True
    # Bot translated text must never go through the user-transcription path.
    assert not any(isinstance(f, TranscriptionFrame) for f in pushed)


@pytest.mark.asyncio
async def test_handle_msg_output_transcription_empty_text_is_noop():
    service = _make_service()
    await service._handle_msg_output_transcription({"text": ""})
    service.push_frame.assert_not_awaited()


def _stub_task_apis(service):
    """Stub create_task/cancel_task so handlers can run without a TaskManager.

    The aggregator schedules a flush task whenever the buffer ends with a
    partial fragment.  In tests we don't need the task to actually run —
    we drive the flush path explicitly via timeout_handler. The stub
    closes the coroutine to avoid "coroutine was never awaited" warnings.
    """

    def _close_coro(coro, *args, **kwargs):
        coro.close()
        return MagicMock()

    service.create_task = MagicMock(side_effect=_close_coro)
    service.cancel_task = AsyncMock()


@pytest.mark.asyncio
async def test_handle_msg_input_transcription_flushes_on_sentence_marker():
    service = _make_service()
    _stub_task_apis(service)
    # Fragments arrive without a final marker, then a marker arrives.
    await service._handle_msg_input_transcription({"text": "hello"})
    await service._handle_msg_input_transcription({"text": " world"})
    await service._handle_msg_input_transcription({"text": ". rest"})

    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    transcriptions = [f for f in pushed if isinstance(f, TranscriptionFrame)]
    assert len(transcriptions) == 1
    assert transcriptions[0].text == "hello world."
    assert transcriptions[0].user_id == ""
    assert transcriptions[0].finalized is True
    # User transcription must be sent UPSTREAM (mirrors pipecat GeminiLive).
    upstream = [
        c
        for c in service.push_frame.await_args_list
        if len(c.args) > 1 and c.args[1] == FrameDirection.UPSTREAM
    ]
    assert len(upstream) == 1
    # Tail remains buffered for the next chunk / timeout flush.
    assert service._user_transcription_buffer == " rest"


@pytest.mark.asyncio
async def test_handle_msg_input_transcription_empty_text_is_noop():
    service = _make_service()
    _stub_task_apis(service)
    await service._handle_msg_input_transcription({"text": ""})
    service.push_frame.assert_not_awaited()
    assert service._user_transcription_buffer == ""


@pytest.mark.asyncio
async def test_transcription_timeout_handler_flushes_buffer():
    service = _make_service()
    service._user_transcription_buffer = "partial fragment"
    # Patch sleep to skip the 0.5s wait.
    with patch(
        "api.services.pipecat.realtime.gemini_live_translate.asyncio.sleep",
        new=AsyncMock(),
    ):
        await service._transcription_timeout_handler()
    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    transcriptions = [f for f in pushed if isinstance(f, TranscriptionFrame)]
    assert len(transcriptions) == 1
    assert transcriptions[0].text == "partial fragment"
    assert transcriptions[0].user_id == ""
    assert service._user_transcription_buffer == ""


@pytest.mark.asyncio
async def test_handle_server_message_dispatches_input_transcription():
    service = _make_service()
    _stub_task_apis(service)
    await service._handle_server_message(
        {"serverContent": {"inputTranscription": {"text": "hola."}}}
    )
    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    assert any(
        isinstance(f, TranscriptionFrame) and f.text == "hola." and f.user_id == ""
        for f in pushed
    )


@pytest.mark.asyncio
async def test_handle_msg_turn_complete_closes_bot_turn():
    service = _make_service()
    service._bot_is_responding = True
    await service._handle_msg_turn_complete()
    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    assert isinstance(pushed[0], TTSStoppedFrame)
    assert isinstance(pushed[1], LLMFullResponseEndFrame)
    assert service._bot_is_responding is False


@pytest.mark.asyncio
async def test_handle_msg_turn_complete_idle_is_noop():
    service = _make_service()
    await service._handle_msg_turn_complete()
    service.push_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_server_message_tool_call_raises():
    service = _make_service()
    with pytest.raises(UnsupportedRealtimeFeatureError):
        await service._handle_server_message({"toolCall": {"functionCalls": []}})


@pytest.mark.asyncio
async def test_handle_server_message_go_away_logs_only():
    service = _make_service()
    await service._handle_server_message({"goAway": {"timeLeft": "5s"}})
    service.push_frame.assert_not_awaited()
    service.broadcast_interruption.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_server_message_setup_complete_is_noop():
    service = _make_service()
    await service._handle_server_message({"setupComplete": {}})
    service.push_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_server_message_interrupted_broadcasts_and_stops_bot():
    service = _make_service()
    service._bot_is_responding = True
    await service._handle_server_message({"serverContent": {"interrupted": True}})
    service.broadcast_interruption.assert_awaited_once()
    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    assert any(isinstance(f, BotStoppedSpeakingFrame) for f in pushed)
    assert service._bot_is_responding is False


@pytest.mark.asyncio
async def test_send_user_audio_wire_format():
    service = _make_service()
    fake_ws = AsyncMock()
    service._websocket = fake_ws
    frame = InputAudioRawFrame(audio=b"\xaa\xbb", sample_rate=16000, num_channels=1)
    await service._send_user_audio(frame)

    fake_ws.send.assert_awaited_once()
    sent = json.loads(fake_ws.send.await_args.args[0])
    # Per https://ai.google.dev/gemini-api/docs/live-api/live-translate#sending_audio
    # the wire format is a single ``audio`` object, not the legacy ``mediaChunks`` list.
    assert "mediaChunks" not in sent["realtimeInput"]
    audio = sent["realtimeInput"]["audio"]
    assert audio["mimeType"] == "audio/pcm;rate=16000"
    assert base64.b64decode(audio["data"]) == b"\xaa\xbb"


@pytest.mark.asyncio
async def test_send_user_audio_skips_when_disconnecting():
    service = _make_service()
    service._websocket = AsyncMock()
    service._disconnecting = True
    frame = InputAudioRawFrame(audio=b"x", sample_rate=16000, num_channels=1)
    await service._send_user_audio(frame)
    service._websocket.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_connection_error_pushes_error_after_budget():
    service = _make_service()
    for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
        retry = await service._handle_connection_error(RuntimeError("boom"))
        assert retry is True
    # The final failure exhausts the budget.
    retry = await service._handle_connection_error(RuntimeError("boom"))
    assert retry is False
    service.push_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_frame_sends_input_audio_and_pushes_downstream():
    service = _make_service()
    service._websocket = AsyncMock()
    frame = InputAudioRawFrame(audio=b"\x00", sample_rate=16000, num_channels=1)

    with patch.object(type(service).__mro__[1], "process_frame", new=AsyncMock()):
        await service.process_frame(frame, FrameDirection.DOWNSTREAM)

    service._websocket.send.assert_awaited_once()
    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    assert any(f is frame for f in pushed)
