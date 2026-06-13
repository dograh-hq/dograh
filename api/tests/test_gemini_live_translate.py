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
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMSetToolChoiceFrame,
    LLMSetToolsFrame,
    LLMUpdateSettingsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
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
    assert setup == {
        "setup": {
            "model": "models/gemini-3.5-live-translate-preview",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "translationConfig": {"targetLanguageCode": "pl"},
            },
            "outputAudioTranscription": {},
        }
    }
    # Input transcription is intentionally omitted (Q2 standing decision).
    assert "inputAudioTranscription" not in setup["setup"]


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
async def test_handle_msg_output_transcription_emits_bot_transcription():
    service = _make_service()
    await service._handle_msg_output_transcription({"text": "Hola mundo"})

    pushed = [c.args[0] for c in service.push_frame.await_args_list]
    # Vertex-parity bracket: TTSStartedFrame + LLMFullResponseStartFrame first.
    assert isinstance(pushed[0], TTSStartedFrame)
    assert isinstance(pushed[1], LLMFullResponseStartFrame)
    transcription = pushed[2]
    assert isinstance(transcription, TranscriptionFrame)
    assert transcription.text == "Hola mundo"
    assert transcription.user_id == "bot"
    assert transcription.finalized is True


@pytest.mark.asyncio
async def test_handle_msg_output_transcription_empty_text_is_noop():
    service = _make_service()
    await service._handle_msg_output_transcription({"text": ""})
    service.push_frame.assert_not_awaited()


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
    chunk = sent["realtimeInput"]["mediaChunks"][0]
    assert chunk["mimeType"] == "audio/pcm;rate=16000"
    assert base64.b64decode(chunk["data"]) == b"\xaa\xbb"


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
