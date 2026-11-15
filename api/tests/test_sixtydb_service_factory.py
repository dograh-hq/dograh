import asyncio
import base64
import contextlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pipecat.frames.frames import ErrorFrame, TTSStoppedFrame
from websockets.protocol import State

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    REGISTRY,
    ServiceProviders,
    ServiceType,
    SixtydbLLMConfiguration,
    SixtydbSTTConfiguration,
    SixtydbTTSConfiguration,
)
from api.services.pipecat import sixtydb_stt as sixtydb_stt_module
from api.services.pipecat import sixtydb_tts as sixtydb_tts_module
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_llm_service_from_provider,
    create_stt_service,
    create_tts_service,
)
from api.services.pipecat.sixtydb_stt import (
    SixtydbSTTService,
    SixtydbSTTSettings,
    sixtydb_stt_websocket_url,
)
from api.services.pipecat.sixtydb_tts import (
    SixtydbTTSService,
    sixtydb_tts_websocket_url,
)


def audio_config():
    return AudioConfig(transport_in_sample_rate=16000, transport_out_sample_rate=16000)


class TestSixtydbLLM:
    def test_configuration_defaults(self):
        config = SixtydbLLMConfiguration(api_key="test-key")

        assert config.provider == ServiceProviders.SIXTY_DB
        assert config.model == "60db-tiny"
        assert config.base_url == "https://api.60db.ai"
        assert config.temperature is None

    def test_is_registered_llm_provider(self):
        assert (
            REGISTRY[ServiceType.LLM][ServiceProviders.SIXTY_DB]
            is SixtydbLLMConfiguration
        )

    def test_factory_uses_openai_compatible_endpoint(self):
        with patch(
            "api.services.pipecat.service_factory.OpenAILLMService"
        ) as mock_service:
            create_llm_service_from_provider(
                provider=ServiceProviders.SIXTY_DB.value,
                model="60db-tiny",
                api_key="test-key",
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["base_url"] == "https://api.60db.ai/v1"
        assert kwargs["settings"].model == "60db-tiny"


class TestSixtydbTTS:
    def test_configuration_defaults(self):
        config = SixtydbTTSConfiguration(api_key="test-key")

        assert config.provider == ServiceProviders.SIXTY_DB
        assert config.model == "60db-tts"
        assert config.voice == "84982e79-c596-4883-b6fa-9af334767aef"
        assert config.speed == 1.0
        assert config.stability == 50
        assert config.similarity == 75
        assert config.base_url == "https://api.60db.ai"

        voice_schema = config.model_json_schema()["properties"]["voice"]
        assert voice_schema["voice_catalog"] == "dropdown"
        assert voice_schema["allow_custom_input"] is True

    def test_is_registered_tts_provider(self):
        assert (
            REGISTRY[ServiceType.TTS][ServiceProviders.SIXTY_DB]
            is SixtydbTTSConfiguration
        )

    def test_factory_passes_voice_controls_and_sample_rate(self):
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.SIXTY_DB.value,
                api_key="test-key",
                model="60db-tts",
                voice="voice-id",
                speed=1.25,
                stability=0,
                similarity=80,
                base_url="https://api.60db.ai",
            )
        )

        with patch(
            "api.services.pipecat.service_factory.SixtydbTTSService"
        ) as mock_service:
            mock_service.InputParams = SixtydbTTSService.InputParams
            create_tts_service(user_config, audio_config())

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["base_url"] == "https://api.60db.ai"
        assert kwargs["sample_rate"] == 16000
        assert kwargs["params"].voice_id == "voice-id"
        assert kwargs["params"].speed == 1.25
        assert kwargs["params"].stability == 0
        assert kwargs["params"].similarity == 80

    def test_tts_websocket_url_contains_no_credential(self):
        url = sixtydb_tts_websocket_url("https://api.60db.ai")
        assert url == "wss://api.60db.ai/ws/tts"

    @pytest.mark.asyncio
    async def test_run_tts_creates_flushes_and_closes_a_context(self):
        service = SixtydbTTSService(api_key="test-key", sample_rate=16000)
        service._sample_rate = 16000
        websocket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
        service._websocket = websocket
        service.start_tts_usage_metrics = AsyncMock()

        async for _frame in service.run_tts("Hello", context_id="ctx-1"):
            pass

        sent = [json.loads(call.args[0]) for call in websocket.send.call_args_list]
        assert sent == [
            {
                "create_context": {
                    "context_id": "ctx-1",
                    "voice_id": SixtydbTTSService.InputParams().voice_id,
                    "audio_config": {
                        "audio_encoding": "LINEAR16",
                        "sample_rate_hertz": 16000,
                    },
                    "speed": SixtydbTTSService.InputParams().speed,
                    "stability": SixtydbTTSService.InputParams().stability,
                    "similarity": SixtydbTTSService.InputParams().similarity,
                }
            }
        ]
        service.start_tts_usage_metrics.assert_awaited_once_with("Hello")

        await service.create_audio_context("ctx-1")
        await service._process_json_message({"context_created": True})
        sent = [json.loads(call.args[0]) for call in websocket.send.call_args_list][1:]
        assert sent[0]["send_text"] == {"context_id": "ctx-1", "text": "Hello"}
        assert sent[1]["flush_context"] == {"context_id": "ctx-1"}

    @pytest.mark.asyncio
    async def test_provider_error_stops_pending_tts_context(self):
        from pipecat.frames.frames import TTSStoppedFrame

        service = SixtydbTTSService(api_key="test-key")
        context_id = "ctx-error"
        await service.create_audio_context(context_id)
        service.stop_all_metrics = AsyncMock()
        service.push_error = AsyncMock()
        service._active_context_id = context_id

        await service._process_json_message(
            {"type": "error", "context_id": context_id, "error": "voice not found"}
        )

        items = []
        while True:
            item = await service._audio_context_manager.get(context_id, timeout=1)
            items.append(item)
            if item is None:
                break
        assert any(isinstance(item, TTSStoppedFrame) for item in items)
        service.stop_all_metrics.assert_awaited_once()
        service.push_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tts_media_without_context_id_uses_active_context(self):
        service = SixtydbTTSService(api_key="test-key", sample_rate=16000)
        service._sample_rate = 16000
        websocket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
        service._websocket = websocket
        service.start_tts_usage_metrics = AsyncMock()
        service.stop_ttfb_metrics = AsyncMock()
        context_id = "ctx-media"
        await service.create_audio_context(context_id)

        async for _frame in service.run_tts("Hello", context_id=context_id):
            pass
        await service._process_json_message({"type": "context_created"})
        await service._process_json_message(
            {
                "audio_chunk": {
                    "audioContent": base64.b64encode(b"audio-byte").decode("ascii")
                }
            }
        )
        await service._process_json_message({"flush_completed": True})

        assert service._audio_context_manager._contexts[context_id].qsize() == 3
        items = []
        while True:
            item = await service._audio_context_manager.get(context_id, timeout=1)
            items.append(item)
            if item is None:
                break
        assert any(getattr(item, "audio", None) == b"audio-byte" for item in items)
        assert any(isinstance(item, TTSStoppedFrame) for item in items)
        service.stop_ttfb_metrics.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_overlapping_tts_contexts_keep_ordered_media_targets(self):
        service = SixtydbTTSService(api_key="test-key", sample_rate=16000)
        service._sample_rate = 16000
        websocket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
        service._websocket = websocket
        service.start_tts_usage_metrics = AsyncMock()
        service.stop_ttfb_metrics = AsyncMock()
        audio = base64.b64encode(b"0123456789").decode("ascii")

        for context_id in ("ctx-first", "ctx-second"):
            await service.create_audio_context(context_id)
            async for _frame in service.run_tts("Hello", context_id=context_id):
                pass

        await service._process_json_message({"context_created": True})
        await service._process_json_message({"audio_chunk": {"audioContent": audio}})
        assert service._audio_context_manager._contexts["ctx-first"].qsize() == 1
        assert service._audio_context_manager._contexts["ctx-second"].qsize() == 0
        assert len(websocket.send.call_args_list) == 3
        await service._process_json_message({"flush_completed": True})
        assert len(websocket.send.call_args_list) == 4

        websocket.state = State.CLOSING
        replacement_socket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
        service._websocket_connect = AsyncMock(return_value=replacement_socket)
        service._wait_for_connection_established = AsyncMock()
        await service._connect_websocket()
        await service._process_json_message({"context_created": True})
        await service._process_json_message({"audio_chunk": {"audioContent": audio}})
        assert service._audio_context_manager._contexts["ctx-second"].qsize() == 1
        assert len(replacement_socket.send.call_args_list) == 3
        await service._process_json_message({"flush_completed": True})
        assert len(replacement_socket.send.call_args_list) == 4

    @pytest.mark.asyncio
    async def test_failed_tts_handshake_closes_socket(self):
        class EmptySocket:
            close = AsyncMock()

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        socket = EmptySocket()
        service = SixtydbTTSService(api_key="test-key")
        service.push_error = AsyncMock()

        service._websocket_connect = AsyncMock(return_value=socket)
        try:
            await service._connect_websocket()
        finally:
            service._websocket_connect.assert_awaited_once()

        socket.close.assert_awaited_once()
        assert service._websocket is None
        service.push_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tts_handshake_times_out_silent_socket(self, monkeypatch):
        monkeypatch.setattr(
            sixtydb_tts_module, "SIXTYDB_TTS_HANDSHAKE_TIMEOUT_SECONDS", 0.01
        )

        class SilentSocket:
            state = State.OPEN

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(30)

        service = SixtydbTTSService(api_key="test-key")
        service._websocket = SilentSocket()

        with pytest.raises(TimeoutError):
            await service._wait_for_connection_established()

    @pytest.mark.asyncio
    async def test_tts_context_abort_closes_active_context(self):
        service = SixtydbTTSService(api_key="test-key")
        context_id = "ctx-aborted"
        await service.create_audio_context(context_id)
        service._active_context_id = context_id
        service.stop_ttfb_metrics = AsyncMock()

        await service._process_json_message(
            {"context_aborted": {"context_id": context_id}}
        )

        items = []
        while True:
            item = await service._audio_context_manager.get(context_id, timeout=1)
            items.append(item)
            if item is None:
                break
        assert any(isinstance(item, TTSStoppedFrame) for item in items)
        assert service._active_context_id is None
        service.stop_ttfb_metrics.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tts_unavailable_connection_terminates_request(self):
        service = SixtydbTTSService(api_key="test-key")
        context_id = "ctx-unavailable"
        await service.create_audio_context(context_id)
        service._websocket = None
        service._connect = AsyncMock()

        frames = [
            frame async for frame in service.run_tts("Hello", context_id=context_id)
        ]

        assert any(isinstance(frame, ErrorFrame) for frame in frames)
        assert any(
            isinstance(frame, TTSStoppedFrame) and frame.context_id == context_id
            for frame in frames
        )
        assert not service._waiting_syntheses
        assert service._active_context_id is None

    @pytest.mark.asyncio
    async def test_tts_setup_error_terminates_audio_context(self):
        service = SixtydbTTSService(api_key="test-key")
        context_id = "ctx-setup-error"
        await service.create_audio_context(context_id)

        async def failing_connect():
            raise RuntimeError("setup failed")

        service._connect = failing_connect

        frames = [
            frame async for frame in service.run_tts("Hello", context_id=context_id)
        ]

        assert any(isinstance(frame, ErrorFrame) for frame in frames)
        assert any(
            isinstance(frame, TTSStoppedFrame) and frame.context_id == context_id
            for frame in frames
        )

    @pytest.mark.asyncio
    async def test_tts_closing_socket_terminates_waiting_context(self):
        service = SixtydbTTSService(api_key="test-key")
        service._websocket = SimpleNamespace(state=State.CLOSING, send=AsyncMock())
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
        service.stop_ttfb_metrics = AsyncMock()
        for context_id in ("ctx-active", "ctx-waiting"):
            await service.create_audio_context(context_id)

        await service._finish_active_context("ctx-active")

        assert service._active_context_id is None
        assert not service._waiting_syntheses
        items = []
        while True:
            item = await service._audio_context_manager.get("ctx-waiting", timeout=1)
            items.append(item)
            if item is None:
                break
        assert any(isinstance(item, TTSStoppedFrame) for item in items)

    @pytest.mark.asyncio
    async def test_tts_transition_send_failure_terminates_waiting_context(self):
        service = SixtydbTTSService(api_key="test-key")
        service._websocket = SimpleNamespace(
            state=State.OPEN,
            send=AsyncMock(side_effect=RuntimeError("peer stopped reading")),
            close=AsyncMock(),
        )
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
        service.push_error = AsyncMock()
        for context_id in ("ctx-active", "ctx-waiting"):
            await service.create_audio_context(context_id)

        await service._finish_active_context("ctx-active")

        assert service._active_context_id is None
        assert not service._waiting_syntheses
        service.push_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tts_expired_active_context_fails_and_preserves_queue(self):
        service = SixtydbTTSService(api_key="test-key")
        service.stop_all_metrics = AsyncMock()
        service.push_error = AsyncMock()
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
        websocket = SimpleNamespace(state=State.OPEN, close=AsyncMock())
        service._websocket = websocket
        for context_id in ("ctx-active", "ctx-waiting"):
            await service.create_audio_context(context_id)

        await service._expire_active_context("ctx-active")

        assert service._active_context_id is None
        assert list(service._waiting_syntheses) == [("ctx-waiting", "second")]
        websocket.close.assert_awaited_once()
        service.stop_all_metrics.assert_awaited_once()
        service.push_error.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tts_active_context_deadline_expires(self, monkeypatch):
        monkeypatch.setattr(
            sixtydb_tts_module,
            "SIXTYDB_TTS_CONTEXT_TIMEOUT_SECONDS",
            0.01,
        )
        service = SixtydbTTSService(api_key="test-key")
        service.stop_all_metrics = AsyncMock()
        service.push_error = AsyncMock()
        websocket = SimpleNamespace(
            state=State.OPEN,
            send=AsyncMock(),
            close=AsyncMock(),
        )
        service._websocket = websocket
        await service.create_audio_context("ctx-active")
        service._waiting_syntheses.append(("ctx-active", "first"))
        service._waiting_syntheses.append(("ctx-waiting", "second"))

        assert await service._start_next_context()
        await asyncio.sleep(0.03)

        websocket.close.assert_awaited_once()
        assert service._active_context_id is None
        assert list(service._waiting_syntheses) == [("ctx-waiting", "second")]

    @pytest.mark.asyncio
    async def test_tts_long_synthesis_with_progress_does_not_hit_fixed_deadline(
        self, monkeypatch
    ):
        service = SixtydbTTSService(api_key="test-key", sample_rate=16000)
        service._sample_rate = 16000
        websocket = SimpleNamespace(
            state=State.OPEN, send=AsyncMock(), close=AsyncMock()
        )
        service._websocket = websocket
        await service.create_audio_context("ctx-active")
        service._waiting_syntheses.append(("ctx-active", "first"))
        service.push_error = AsyncMock()
        audio = base64.b64encode(b"0123456789").decode("ascii")

        assert await service._start_next_context()
        creation_deadline = service._active_timeout_task
        await service._process_json_message({"context_created": True})
        assert service._active_timeout_task is not creation_deadline
        with contextlib.suppress(asyncio.CancelledError):
            await creation_deadline

        request_deadline = service._active_timeout_task
        await service._process_json_message({"audio_chunk": {"audioContent": audio}})
        assert service._active_timeout_task is not request_deadline
        with contextlib.suppress(asyncio.CancelledError):
            await request_deadline

        assert service._active_context_id == "ctx-active"
        assert service.audio_context_available("ctx-active")
        audio_deadline = service._active_timeout_task
        await service._process_json_message({"flush_completed": True})
        assert service._active_context_id is None
        with contextlib.suppress(asyncio.CancelledError):
            await audio_deadline
        service.push_error.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tts_send_failure_closes_registered_audio_context(self):
        service = SixtydbTTSService(api_key="test-key")
        context_id = "ctx-send-failure"
        await service.create_audio_context(context_id)
        service._websocket = SimpleNamespace(
            state=State.OPEN,
            send=AsyncMock(side_effect=RuntimeError("peer stopped reading")),
            close=AsyncMock(),
        )
        service._disconnect = AsyncMock()
        service._connect = AsyncMock()

        frames = [
            frame async for frame in service.run_tts("Hello", context_id=context_id)
        ]

        assert any(isinstance(frame, ErrorFrame) for frame in frames)
        items = []
        while True:
            item = await service._audio_context_manager.get(context_id, timeout=1)
            items.append(item)
            if item is None:
                break
        assert any(isinstance(item, TTSStoppedFrame) for item in items)

    @pytest.mark.asyncio
    async def test_tts_closing_socket_reconnects_before_new_request(self):
        service = SixtydbTTSService(api_key="test-key")
        replacement_socket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
        service._websocket = SimpleNamespace(state=State.CLOSING)

        async def reconnect():
            service._websocket = replacement_socket

        service._connect = reconnect
        frames = [
            frame async for frame in service.run_tts("Hello", context_id="ctx-next")
        ]

        assert not any(isinstance(frame, ErrorFrame) for frame in frames)
        replacement_socket.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tts_context_closed_retains_and_restarts_waiting_context(self):
        service = SixtydbTTSService(api_key="test-key")
        service._websocket = SimpleNamespace(
            state=State.OPEN, send=AsyncMock(), close=AsyncMock()
        )
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
        replacement_socket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
        service.stop_ttfb_metrics = AsyncMock()
        service._websocket_connect = AsyncMock(return_value=replacement_socket)
        service._wait_for_connection_established = AsyncMock()
        for context_id in ("ctx-active", "ctx-waiting"):
            await service.create_audio_context(context_id)

        await service._process_json_message(
            {"context_closed": {"context_id": "ctx-active"}}
        )

        assert service._active_context_id is None
        assert list(service._waiting_syntheses) == [("ctx-waiting", "second")]
        service._websocket.state = State.CLOSING
        await service._connect_websocket()
        assert service._websocket is replacement_socket
        assert service._active_context_id == "ctx-waiting"
        assert not service._waiting_syntheses
        create_message = json.loads(service._websocket.send.call_args.args[0])
        assert create_message["create_context"]["context_id"] == "ctx-waiting"
        service.stop_ttfb_metrics.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tts_flush_completion_closes_context_before_next_synthesis(self):
        service = SixtydbTTSService(api_key="test-key")
        websocket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
        service._websocket = websocket
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
        service.stop_ttfb_metrics = AsyncMock()
        for context_id in ("ctx-active", "ctx-waiting"):
            await service.create_audio_context(context_id)

        await service._process_json_message(
            {"flush_completed": {"context_id": "ctx-active"}}
        )

        assert service._active_context_id is None
        assert list(service._waiting_syntheses) == [("ctx-waiting", "second")]
        websocket.send.assert_awaited_once()
        message = json.loads(websocket.send.call_args.args[0])
        assert message == {"close_context": {"context_id": "ctx-active"}}

    @pytest.mark.asyncio
    async def test_tts_socket_closure_terminates_active_and_retains_queue(self):
        class ClosedSocket:
            state = State.CLOSING

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        class ReplacementSocket:
            state = State.OPEN
            send = AsyncMock()

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        service = SixtydbTTSService(api_key="test-key")
        service._websocket = ClosedSocket()
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
        service.stop_all_metrics = AsyncMock()
        await service.create_audio_context("ctx-active")
        replacement_socket = ReplacementSocket()
        service._websocket_connect = AsyncMock(return_value=replacement_socket)
        service._wait_for_connection_established = AsyncMock()
        service.create_task = asyncio.create_task

        await service._process_messages()

        assert service._socket_recovery_task is not None
        await service._socket_recovery_task
        assert not service._waiting_syntheses
        service.stop_all_metrics.assert_awaited()
        create_message = json.loads(replacement_socket.send.call_args.args[0])
        assert create_message["create_context"]["context_id"] == "ctx-waiting"

    @pytest.mark.asyncio
    async def test_tts_socket_closure_reports_lone_active_synthesis_error(self):
        class ClosedSocket:
            state = State.CLOSING

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        service = SixtydbTTSService(api_key="test-key")
        service._websocket = ClosedSocket()
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service.stop_all_metrics = AsyncMock()
        service.push_error = AsyncMock()
        await service.create_audio_context("ctx-active")

        await service._process_messages()

        assert service._active_context_id is None
        service.stop_all_metrics.assert_awaited_once()
        service.push_error.assert_awaited_once_with(
            error_msg="60db TTS connection closed during synthesis"
        )
        items = []
        while True:
            item = await service._audio_context_manager.get("ctx-active", timeout=1)
            items.append(item)
            if item is None:
                break
        assert any(isinstance(item, TTSStoppedFrame) for item in items)

    @pytest.mark.asyncio
    async def test_interrupted_waiting_tts_context_is_discarded(self):
        service = SixtydbTTSService(api_key="test-key")
        service._websocket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-interrupted", "second"))

        await service.on_audio_context_interrupted("ctx-interrupted")

        assert not service._waiting_syntheses


class TestSixtydbSTT:
    def test_configuration_defaults(self):
        config = SixtydbSTTConfiguration(api_key="test-key")

        assert config.provider == ServiceProviders.SIXTY_DB
        assert config.model == "60db-stt-v01"
        assert config.language == "auto"
        assert config.base_url == "https://api.60db.ai"

    def test_is_registered_stt_provider(self):
        assert (
            REGISTRY[ServiceType.STT][ServiceProviders.SIXTY_DB]
            is SixtydbSTTConfiguration
        )

    def test_factory_passes_language_keywords_and_sample_rate(self):
        user_config = SimpleNamespace(
            stt=SimpleNamespace(
                provider=ServiceProviders.SIXTY_DB.value,
                api_key="test-key",
                model="60db-stt-v01",
                language="hi",
                base_url="https://api.60db.ai",
            )
        )

        with patch(
            "api.services.pipecat.service_factory.SixtydbSTTService"
        ) as mock_service:
            create_stt_service(
                user_config, audio_config(), keyterms=["HDFC", "Fortuner"]
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["base_url"] == "https://api.60db.ai"
        assert kwargs["sample_rate"] == 16000
        assert kwargs["settings"].language == "hi"
        assert kwargs["settings"].keywords == ["HDFC", "Fortuner"]

    def test_start_message_uses_whole_sentence_profile(self):
        service = SixtydbSTTService(
            api_key="test-key",
            sample_rate=16000,
            settings=SixtydbSTTSettings(language="hi", keywords=["HDFC"]),
        )
        service._sample_rate = 16000
        message = json.loads(service._start_message())

        assert message["languages"] == ["hi"]
        assert message["keywords"] == ["HDFC"]
        assert message["config"] == {
            "encoding": "linear",
            "sample_rate": 16000,
            "audio_enhancement": "on",
            "early_final": False,
            "turn_detector": True,
            "utterance_end_ms": 1200,
            "interim_results_frequency": 500,
        }

    def test_auto_detect_omits_language_list(self):
        service = SixtydbSTTService(api_key="test-key", sample_rate=16000)
        service._sample_rate = 16000
        message = json.loads(service._start_message())
        assert message["languages"] is None

    @pytest.mark.asyncio
    async def test_terminal_stt_error_is_reported_to_pipeline(self):
        service = SixtydbSTTService(api_key="test-key")
        pushed = []

        async def capture_frame(frame):
            pushed.append(frame)

        service.push_frame = capture_frame
        await service._process_response(
            {
                "type": "error",
                "terminal": True,
                "error": "Transcription timed out.",
            }
        )

        assert len(pushed) == 1
        assert isinstance(pushed[0], ErrorFrame)
        assert pushed[0].error == "Transcription timed out."

    @pytest.mark.asyncio
    async def test_stt_audio_without_connection_reports_error(self):
        service = SixtydbSTTService(api_key="test-key")
        frames = [frame async for frame in service.run_stt(b"audio")]

        assert len(frames) == 1
        assert isinstance(frames[0], ErrorFrame)
        assert frames[0].error == "60db STT connection is unavailable"

    @pytest.mark.asyncio
    async def test_stt_handshake_rejects_closed_session(self):
        class ClosedSessionSocket:
            delivered = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.delivered:
                    self.delivered = True
                    return json.dumps({"type": "connection_established"})
                raise StopAsyncIteration

            async def send(self, message):
                pass

        service = SixtydbSTTService(api_key="test-key", sample_rate=16000)
        service._websocket = ClosedSessionSocket()

        with pytest.raises(RuntimeError, match="closed before session_started"):
            await service._handshake()

    @pytest.mark.asyncio
    async def test_stt_handshake_times_out_silent_socket(self, monkeypatch):
        monkeypatch.setattr(
            sixtydb_stt_module, "SIXTYDB_STT_HANDSHAKE_TIMEOUT_SECONDS", 0.01
        )

        class SilentSocket:
            state = State.OPEN

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(30)

        service = SixtydbSTTService(api_key="test-key", sample_rate=16000)
        service._websocket = SilentSocket()

        with pytest.raises(TimeoutError):
            await service._handshake()

    @pytest.mark.asyncio
    async def test_failed_stt_handshake_closes_socket(self):
        class EmptySocket:
            close = AsyncMock()

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        socket = EmptySocket()
        service = SixtydbSTTService(api_key="test-key")
        service.push_error = AsyncMock()
        service._call_event_handler = AsyncMock()
        service._websocket_connect = AsyncMock(return_value=socket)

        await service._connect_websocket()

        socket.close.assert_awaited_once()
        assert service._websocket is None

    @pytest.mark.asyncio
    async def test_failed_stt_handshake_does_not_start_receiver(self):
        service = SixtydbSTTService(api_key="test-key")
        service._connect_websocket = AsyncMock()

        def fail_create_task(*args, **kwargs):
            raise AssertionError("receiver must not start after failed handshake")

        service.create_task = fail_create_task
        await service._connect()

        assert service._websocket is None
        assert service._receive_task is None

    @pytest.mark.asyncio
    async def test_stt_teardown_closes_socket_when_stop_send_fails(self):
        websocket = SimpleNamespace(
            state=State.OPEN,
            send=AsyncMock(side_effect=RuntimeError("stop failed")),
            close=AsyncMock(),
        )
        service = SixtydbSTTService(api_key="test-key")
        service._websocket = websocket

        await service._disconnect_websocket()

        websocket.close.assert_awaited_once()
        assert service._websocket is None

    def test_stt_websocket_url_contains_no_credential(self):
        url = sixtydb_stt_websocket_url("https://api.60db.ai")
        assert url == "wss://api.60db.ai/ws/stt"


class TestSixtydbKeyValidation:
    def test_is_registered_for_key_validation(self):
        validator = UserConfigurationValidator()
        assert ServiceProviders.SIXTY_DB.value in validator._validator_map

    @pytest.mark.asyncio
    async def test_sixtydb_validation_does_not_block_the_event_loop(self):
        configuration = SimpleNamespace(
            llm=object(),
            llm_fallback=None,
            is_realtime=False,
            stt=object(),
            tts=object(),
            embeddings=None,
        )
        validator = UserConfigurationValidator()

        def blocking_validation(service, service_name, required=True):
            time.sleep(0.02)
            return []

        validator._validate_service = blocking_validation
        validation = asyncio.create_task(validator.validate(configuration))
        ticks = 0
        while not validation.done():
            await asyncio.sleep(0.01)
            ticks += 1

        assert await validation == {"status": [{"model": "all", "message": "ok"}]}
        assert ticks >= 2

    def test_accepts_valid_key(self):
        validator = UserConfigurationValidator()
        with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            assert validator._check_sixtydb_api_key("60db-tiny", "valid-key") is True

        assert mock_get.call_args.args[0] == "https://api.60db.ai/voices"
        assert (
            mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer valid-key"
        )

    def test_rejects_bad_key(self):
        validator = UserConfigurationValidator()
        with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
            mock_get.return_value.status_code = 401
            with pytest.raises(ValueError):
                validator._check_sixtydb_api_key("60db-tiny", "bad-key")

    def test_network_failure_does_not_block_key_save(self):
        validator = UserConfigurationValidator()
        with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
            mock_get.side_effect = httpx.RequestError("temporary network failure")

            assert validator._check_sixtydb_api_key("60db-tiny", "valid-key") is True

    def test_key_validation_uses_configured_endpoint(self):
        validator = UserConfigurationValidator()
        service_config = SimpleNamespace(base_url="https://proxy.60db.example")
        with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
            mock_get.return_value.status_code = 200

            assert (
                validator._check_sixtydb_api_key(
                    "60db-tiny", "valid-key", service_config
                )
                is True
            )

        assert mock_get.call_args.args[0] == "https://proxy.60db.example/voices"

    def test_key_validation_normalizes_llm_v1_endpoint(self):
        validator = UserConfigurationValidator()
        service_config = SimpleNamespace(base_url="https://api.60db.ai/v1")
        with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
            mock_get.return_value.status_code = 200

            assert (
                validator._check_sixtydb_api_key(
                    "60db-tiny", "valid-key", service_config
                )
                is True
            )

        assert mock_get.call_args.args[0] == "https://api.60db.ai/voices"
