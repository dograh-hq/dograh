from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pipecat.utils.types import is_given

from api.services.configuration.registry import (
    ELEVENLABS_TTS_MODELS,
    ServiceProviders,
)
from api.services.pipecat.elevenlabs_tts import (
    ElevenLabsOwnedSessionHttpTTSService,
    requires_http_streaming,
)
from api.services.pipecat.service_factory import create_tts_service

AUDIO_CONFIG = SimpleNamespace(transport_in_sample_rate=16000)


def _user_config(model: str, **tts_overrides):
    tts = SimpleNamespace(
        provider=ServiceProviders.ELEVENLABS.value,
        api_key="test-key",
        model=model,
        voice="21m00Tcm4TlvDq8ikWAM",
        speed=1.0,
        base_url="https://api.elevenlabs.io",
    )
    for key, value in tts_overrides.items():
        setattr(tts, key, value)
    return SimpleNamespace(tts=tts)


class TestRequiresHttpStreaming:
    @pytest.mark.parametrize(
        "model",
        ["eleven_v4_turbo", "eleven_v4", "eleven_v3", "eleven_v3_conversational"],
    )
    def test_v3_and_v4_models_need_http(self, model):
        assert requires_http_streaming(model) is True

    @pytest.mark.parametrize(
        "model",
        ["eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_multilingual_v2", "", None],
    )
    def test_websocket_models_do_not(self, model):
        assert requires_http_streaming(model) is False


class TestElevenLabsTTSServiceFactory:
    def test_v4_models_are_listed(self):
        assert ELEVENLABS_TTS_MODELS[0] == "eleven_flash_v2_5"
        assert {"eleven_v4_turbo", "eleven_v4"} <= set(ELEVENLABS_TTS_MODELS)

    def test_flash_model_keeps_the_websocket_service(self):
        with (
            patch(
                "api.services.pipecat.service_factory.ElevenLabsTTSService"
            ) as ws_service,
            patch(
                "api.services.pipecat.service_factory.ElevenLabsOwnedSessionHttpTTSService"
            ) as http_service,
        ):
            create_tts_service(_user_config("eleven_flash_v2_5"), AUDIO_CONFIG)

        assert ws_service.call_count == 1
        assert http_service.call_count == 0
        assert ws_service.call_args.kwargs["url"] == "wss://api.elevenlabs.io"

    @pytest.mark.parametrize("model", ["eleven_v4_turbo", "eleven_v4", "eleven_v3"])
    def test_v3_and_v4_models_use_the_http_service(self, model):
        with (
            patch("api.services.pipecat.service_factory.aiohttp.ClientSession"),
            patch(
                "api.services.pipecat.service_factory.ElevenLabsTTSService"
            ) as ws_service,
            patch(
                "api.services.pipecat.service_factory.ElevenLabsOwnedSessionHttpTTSService"
            ) as http_service,
        ):
            create_tts_service(_user_config(model), AUDIO_CONFIG)

        assert ws_service.call_count == 0
        assert http_service.call_count == 1
        kwargs = http_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["base_url"] == "https://api.elevenlabs.io"
        assert kwargs["aiohttp_session"] is not None
        settings = kwargs["settings"]
        assert settings.model == model
        assert settings.voice == "21m00Tcm4TlvDq8ikWAM"
        assert settings.stability == 0.8
        assert settings.similarity_boost == 0.75

    def test_http_service_sends_only_stability_and_similarity(self):
        with (
            patch("api.services.pipecat.service_factory.aiohttp.ClientSession"),
            patch(
                "api.services.pipecat.service_factory.ElevenLabsOwnedSessionHttpTTSService"
            ) as http_service,
        ):
            create_tts_service(_user_config("eleven_v4_turbo", speed=0.9), AUDIO_CONFIG)

        settings = http_service.call_args.kwargs["settings"]
        for unsupported in ("style", "speed", "use_speaker_boost"):
            assert not is_given(getattr(settings, unsupported))

    def test_http_service_keeps_legacy_voice_format_and_residency_url(self):
        with (
            patch("api.services.pipecat.service_factory.aiohttp.ClientSession"),
            patch(
                "api.services.pipecat.service_factory.ElevenLabsOwnedSessionHttpTTSService"
            ) as http_service,
        ):
            create_tts_service(
                _user_config(
                    "eleven_v4_turbo",
                    voice="Rachel - 21m00Tcm4TlvDq8ikWAM",
                    base_url="https://api.eu.residency.elevenlabs.io/",
                ),
                AUDIO_CONFIG,
            )

        kwargs = http_service.call_args.kwargs
        assert kwargs["base_url"] == "https://api.eu.residency.elevenlabs.io"
        assert kwargs["settings"].voice == "21m00Tcm4TlvDq8ikWAM"

    def test_http_service_does_not_consult_cache(self):
        with (
            patch("api.services.pipecat.service_factory.aiohttp.ClientSession"),
            patch(
                "api.services.pipecat.service_factory.ElevenLabsOwnedSessionHttpTTSService"
            ),
            patch("api.services.pipecat.service_factory.get_speech_cache") as get_cache,
        ):
            create_tts_service(
                _user_config("eleven_v4_turbo"),
                AUDIO_CONFIG,
                organization_id=7,
                tts_cache_enabled=True,
            )
        get_cache.assert_not_called()


class TestElevenLabsOwnedSessionHttpTTSService:
    @staticmethod
    def _session():
        session = MagicMock()
        session.closed = False
        session.close = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_cleanup_closes_the_owned_session(self):
        session = self._session()
        service = ElevenLabsOwnedSessionHttpTTSService(
            api_key="test-key", aiohttp_session=session
        )

        with patch(
            "pipecat.services.elevenlabs.tts.ElevenLabsHttpTTSService.cleanup",
            new=AsyncMock(),
        ):
            await service.cleanup()

        session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_closes_the_session_when_parent_cleanup_fails(self):
        session = self._session()
        service = ElevenLabsOwnedSessionHttpTTSService(
            api_key="test-key", aiohttp_session=session
        )

        with (
            patch(
                "pipecat.services.elevenlabs.tts.ElevenLabsHttpTTSService.cleanup",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            pytest.raises(RuntimeError),
        ):
            await service.cleanup()

        session.close.assert_awaited_once()
