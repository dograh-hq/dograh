import base64
import json
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
        assert config.base_url == "https://api.60db.ai/v1"
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

        await service._process_json_message({"context_created": True})
        await service._process_json_message({"audio_chunk": {"audioContent": audio}})
        assert service._audio_context_manager._contexts["ctx-second"].qsize() == 1
        assert len(websocket.send.call_args_list) == 6
        await service._process_json_message({"flush_completed": True})

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
    async def test_tts_closing_socket_terminates_waiting_context(self):
        service = SixtydbTTSService(api_key="test-key")
        service._websocket = SimpleNamespace(state=State.CLOSING, send=AsyncMock())
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
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
    async def test_tts_context_closed_retains_and_restarts_waiting_context(self):
        service = SixtydbTTSService(api_key="test-key")
        service._websocket = SimpleNamespace(
            state=State.OPEN, send=AsyncMock(), close=AsyncMock()
        )
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
        replacement_socket = SimpleNamespace(state=State.OPEN, send=AsyncMock())
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

    @pytest.mark.asyncio
    async def test_tts_socket_closure_terminates_active_and_retains_queue(self):
        class ClosedSocket:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        service = SixtydbTTSService(api_key="test-key")
        service._websocket = ClosedSocket()
        service._active_context_id = "ctx-active"
        service._active_text = "first"
        service._waiting_syntheses.append(("ctx-waiting", "second"))
        await service.create_audio_context("ctx-active")

        await service._process_messages()

        assert service._active_context_id is None
        assert list(service._waiting_syntheses) == [("ctx-waiting", "second")]

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

    def test_stt_websocket_url_contains_no_credential(self):
        url = sixtydb_stt_websocket_url("https://api.60db.ai")
        assert url == "wss://api.60db.ai/ws/stt"


class TestSixtydbKeyValidation:
    def test_is_registered_for_key_validation(self):
        validator = UserConfigurationValidator()
        assert ServiceProviders.SIXTY_DB.value in validator._validator_map

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
