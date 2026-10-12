from types import SimpleNamespace
from unittest.mock import patch

import pytest
from loguru import logger
from pipecat.services.minimax.llm import MiniMaxLLMService as RealMiniMaxLLMService
from pipecat.services.minimax.tts import MiniMaxTTSService

from api.services.configuration.registry import (
    MiniMaxLLMConfiguration,
    MiniMaxTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.service_factory import (
    create_llm_service_from_provider,
    create_tts_service,
)


class TestMiniMaxLLMConfiguration:
    def test_default_values(self):
        config = MiniMaxLLMConfiguration(api_key="test-key")
        assert config.provider == ServiceProviders.MINIMAX
        assert config.model == "MiniMax-M2.7"
        assert config.base_url == "https://api.minimax.io/v1"

    def test_custom_model(self):
        config = MiniMaxLLMConfiguration(
            api_key="test-key", model="MiniMax-M2.7-highspeed"
        )
        assert config.model == "MiniMax-M2.7-highspeed"

    def test_custom_base_url(self):
        config = MiniMaxLLMConfiguration(
            api_key="test-key", base_url="https://api.minimaxi.com/v1"
        )
        assert config.base_url == "https://api.minimaxi.com/v1"


class TestMiniMaxTTSConfiguration:
    def test_default_values(self):
        config = MiniMaxTTSConfiguration(api_key="test-key", group_id="test-group")
        assert config.provider == ServiceProviders.MINIMAX
        assert config.model == "speech-2.8-hd"
        assert config.voice == "English_Graceful_Lady"
        assert config.speed == 1.0
        assert config.group_id == "test-group"


class TestMiniMaxLLMServiceFactory:
    def test_create_minimax_llm_service_uses_openai_compatible(self):
        with patch(
            "api.services.pipecat.service_factory.MiniMaxLLMService"
        ) as mock_service:
            mock_service.Settings = RealMiniMaxLLMService.Settings
            create_llm_service_from_provider(
                provider=ServiceProviders.MINIMAX.value,
                model="MiniMax-M2.7",
                api_key="test-key",
            )

        assert mock_service.call_count == 1
        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["base_url"] == "https://api.minimax.io/v1"
        assert kwargs["settings"].model == "MiniMax-M2.7"
        assert kwargs["settings"].temperature == 1.0

    def test_create_minimax_llm_service_custom_base_url(self):
        with patch(
            "api.services.pipecat.service_factory.MiniMaxLLMService"
        ) as mock_service:
            mock_service.Settings = RealMiniMaxLLMService.Settings
            create_llm_service_from_provider(
                provider=ServiceProviders.MINIMAX.value,
                model="MiniMax-M2.7-highspeed",
                api_key="test-key",
                base_url="https://api.minimaxi.com/v1",
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["base_url"] == "https://api.minimaxi.com/v1"
        assert kwargs["settings"].model == "MiniMax-M2.7-highspeed"

    def test_create_minimax_llm_service_passes_user_temperature(self):
        with patch(
            "api.services.pipecat.service_factory.MiniMaxLLMService"
        ) as mock_service:
            mock_service.Settings = RealMiniMaxLLMService.Settings
            create_llm_service_from_provider(
                provider=ServiceProviders.MINIMAX.value,
                model="MiniMax-M2.7",
                api_key="test-key",
                temperature=0.3,
            )
        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].temperature == 0.3


class TestMiniMaxTTSServiceFactory:
    def test_create_minimax_tts_service(self):
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.MINIMAX.value,
                api_key="test-key",
                model="speech-2.8-hd",
                voice="English_Graceful_Lady",
                speed=1.0,
                base_url="https://api.minimax.io/v1",
                group_id="test-group",
            )
        )
        audio_config = SimpleNamespace(transport_in_sample_rate=16000)

        with patch(
            "api.services.pipecat.service_factory.MiniMaxTTSService"
        ) as mock_service:
            create_tts_service(user_config, audio_config)

        assert mock_service.call_count == 1
        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["url"] == "wss://api.minimax.io/ws/v1/t2a_v2_bidi"
        assert kwargs["settings"].model == "speech-2.8-hd"
        assert kwargs["settings"].voice == "English_Graceful_Lady"
        assert kwargs["settings"].speed == 1.0

    def test_speech_cache_setting_only_logs_a_warning(self):
        warnings = []
        sink = logger.add(
            lambda m: warnings.append(m.record["message"]), level="WARNING"
        )
        try:
            service = create_tts_service(
                SimpleNamespace(
                    tts=MiniMaxTTSConfiguration(
                        api_key="test-key", group_id="test-group"
                    )
                ),
                SimpleNamespace(transport_in_sample_rate=16000),
                organization_id=7,
                tts_cache_enabled=True,
            )
        finally:
            logger.remove(sink)

        assert type(service) is MiniMaxTTSService
        assert any("does not use the speech cache" in w for w in warnings)

    @pytest.mark.parametrize(
        ("base_url", "websocket_url"),
        [
            (
                "https://api.minimax.io/v1/t2a_v2",
                "wss://api.minimax.io/ws/v1/t2a_v2_bidi",
            ),
            (
                "https://api-uw.minimax.io/v1",
                "wss://api-uw.minimax.io/ws/v1/t2a_v2_bidi",
            ),
        ],
    )
    def test_websocket_url_follows_the_configured_host(self, base_url, websocket_url):
        user_config = SimpleNamespace(
            tts=MiniMaxTTSConfiguration(
                api_key="test-key",
                group_id="test-group",
                model="speech-2.8-turbo",
                voice="Italian_DiligentLeader",
                speed=1.3,
                base_url=base_url,
            )
        )
        with patch("api.services.pipecat.service_factory.MiniMaxTTSService") as service:
            create_tts_service(
                user_config,
                SimpleNamespace(transport_in_sample_rate=8000),
                organization_id=7,
                tts_cache_enabled=False,
            )

        kwargs = service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["url"] == websocket_url
        assert kwargs["settings"].model == "speech-2.8-turbo"
        assert kwargs["settings"].voice == "Italian_DiligentLeader"
        assert kwargs["settings"].speed == 1.3
