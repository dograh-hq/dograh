from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.services.minimax.llm import MiniMaxLLMService as RealMiniMaxLLMService
from pipecat.services.minimax.tts import MiniMaxTTSService
from pydantic import ValidationError

from api.services.configuration.model_connections import model_connection_catalog
from api.services.configuration.registry import (
    MiniMaxLLMConfiguration,
    MiniMaxTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.minimax_tts import MiniMaxCachingWebsocketTTSService
from api.services.pipecat.service_factory import (
    create_llm_service_from_provider,
    create_tts_service,
)
from api.services.pipecat.tts_cache.cache import SpeechCache
from api.services.pipecat.tts_cache.models import CachePolicy
from api.tests.test_tts_cache import MemoryBackend


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
        assert config.volume == 1.0
        assert config.group_id == "test-group"

    def test_volume_is_exposed_in_model_settings(self):
        entry = model_connection_catalog()["services"]["tts"]["minimax"]
        volume = entry["settings_schema"]["properties"]["volume"]
        assert volume["type"] == "number"
        assert volume["default"] == 1.0
        assert volume["exclusiveMinimum"] == 0.0
        assert volume["maximum"] == 10.0

    @pytest.mark.parametrize("volume", [0.0, -0.1, 10.01, float("nan"), float("inf")])
    def test_invalid_volume(self, volume):
        with pytest.raises(ValidationError, match="volume"):
            MiniMaxTTSConfiguration(
                api_key="test-key", group_id="test-group", volume=volume
            )

    @pytest.mark.parametrize("entry", ["Dograh", "/(ˈdoʊɡrɑː)", "Dograh/", " / "])
    def test_pronunciation_entries_need_both_sides(self, entry):
        with pytest.raises(ValidationError, match="original/replacement"):
            MiniMaxTTSConfiguration(
                api_key="test-key", group_id="test-group", pronunciation_dict=[entry]
            )

    def test_text_normalization_mode_is_one_of_minimax_modes(self):
        with pytest.raises(ValidationError, match="text_normalization_mode"):
            MiniMaxTTSConfiguration(
                api_key="test-key",
                group_id="test-group",
                text_normalization_mode="aggressive",
            )

    def test_new_settings_are_exposed_in_model_settings(self):
        properties = model_connection_catalog()["services"]["tts"]["minimax"][
            "settings_schema"
        ]["properties"]
        assert properties["pronunciation_dict"]["type"] == "array"
        assert properties["pronunciation_dict"]["items"] == {"type": "string"}
        assert properties["text_normalization_mode"]["examples"] == ["basic", "quality"]


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
        assert kwargs["settings"].volume == 1.0

    @pytest.mark.parametrize(
        "settings", [{}, {"volume": 0.01}, {"volume": 1.16}, {"volume": 10.0}]
    )
    def test_saved_volume_reaches_websocket_request(self, settings):
        config = MiniMaxTTSConfiguration(
            api_key="test-key", group_id="test-group", **settings
        )
        restored = MiniMaxTTSConfiguration.model_validate_json(config.model_dump_json())
        service = create_tts_service(
            SimpleNamespace(tts=restored),
            SimpleNamespace(transport_in_sample_rate=16000),
        )

        assert service._task_start_message()["voice_setting"]["vol"] == settings.get(
            "volume", 1.0
        )

    def test_speech_cache_setting_builds_the_caching_service(self):
        cache = SpeechCache(MemoryBackend(), CachePolicy())
        with patch(
            "api.services.pipecat.service_factory.get_speech_cache", return_value=cache
        ) as get_cache:
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

        get_cache.assert_called_once_with(7, enabled=True)
        assert type(service) is MiniMaxCachingWebsocketTTSService
        assert service._speech_cache is cache
        assert service._organization_id == 7

    def test_without_the_speech_cache_the_plain_service_is_built(self):
        service = create_tts_service(
            SimpleNamespace(
                tts=MiniMaxTTSConfiguration(api_key="test-key", group_id="test-group")
            ),
            SimpleNamespace(transport_in_sample_rate=16000),
            organization_id=7,
            tts_cache_enabled=False,
        )

        assert type(service) is MiniMaxTTSService

    def test_pronunciation_and_normalization_reach_task_start(self):
        config = MiniMaxTTSConfiguration(
            api_key="test-key",
            group_id="test-group",
            text_normalization_mode="basic",
            pronunciation_dict=[" Dograh/(ˈdoʊɡrɑː) ", "", "SRL/esse erre elle"],
        )
        restored = MiniMaxTTSConfiguration.model_validate_json(config.model_dump_json())
        service = create_tts_service(
            SimpleNamespace(tts=restored),
            SimpleNamespace(transport_in_sample_rate=16000),
        )

        message = service._task_start_message()
        assert message["text_normalization_mode"] == "basic"
        assert message["pronunciation_dict"] == {
            "tone": ["Dograh/(ˈdoʊɡrɑː)", "SRL/esse erre elle"]
        }

    def test_unset_pronunciation_and_normalization_are_not_sent(self):
        service = create_tts_service(
            SimpleNamespace(
                tts=MiniMaxTTSConfiguration(api_key="test-key", group_id="test-group")
            ),
            SimpleNamespace(transport_in_sample_rate=16000),
        )

        message = service._task_start_message()
        assert "text_normalization_mode" not in message
        assert "pronunciation_dict" not in message

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
