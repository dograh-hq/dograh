from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.services.sarvam.llm import SarvamLLMService as RealSarvamLLMService
from pipecat.transcriptions.language import Language

from api.services.configuration.registry import (
    SarvamLLMConfiguration,
    SarvamTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_llm_service_from_provider,
    create_stt_service,
    create_tts_service,
)


class TestSarvamLLMConfiguration:
    def test_default_values(self):
        config = SarvamLLMConfiguration(api_key="test-key")
        assert config.provider == ServiceProviders.SARVAM
        assert config.model == "sarvam-30b"
        assert config.temperature == 0.5

    def test_custom_model(self):
        config = SarvamLLMConfiguration(api_key="test-key", model="sarvam-105b")
        assert config.model == "sarvam-105b"


class TestSarvamLLMServiceFactory:
    def test_create_sarvam_llm_service(self):
        with patch(
            "api.services.pipecat.service_factory.SarvamLLMService"
        ) as mock_service:
            mock_service.Settings = RealSarvamLLMService.Settings
            create_llm_service_from_provider(
                provider=ServiceProviders.SARVAM.value,
                model="sarvam-30b",
                api_key="test-key",
            )

        assert mock_service.call_count == 1
        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["settings"].model == "sarvam-30b"
        assert kwargs["settings"].temperature == 0.5

    def test_create_sarvam_llm_service_passes_user_temperature(self):
        with patch(
            "api.services.pipecat.service_factory.SarvamLLMService"
        ) as mock_service:
            mock_service.Settings = RealSarvamLLMService.Settings
            create_llm_service_from_provider(
                provider=ServiceProviders.SARVAM.value,
                model="sarvam-30b",
                api_key="test-key",
                temperature=0.8,
            )

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].temperature == 0.8

    def test_create_llm_service_extracts_sarvam_temperature(self):
        user_config = SimpleNamespace(
            llm=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                model="sarvam-30b",
                api_key="test-key",
                temperature=0.7,
            )
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamLLMService"
        ) as mock_service:
            mock_service.Settings = RealSarvamLLMService.Settings
            create_llm_service(user_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].temperature == 0.7


class TestSarvamSTTServiceFactory:
    @pytest.mark.parametrize(
        "input_language,expected_language",
        [
            ("unknown", None),
            (None, None),
            ("hi-IN", Language.HI_IN),
            ("ne-IN", "ne-IN"),
        ],
    )
    def test_stt_language_mapping(self, input_language, expected_language):
        user_config = SimpleNamespace(
            stt=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                model="saaras:v3",
                api_key="test-key",
                language=input_language,
            )
        )
        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamSTTService"
        ) as mock_service:
            create_stt_service(user_config, audio_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].language == expected_language


class TestSarvamTTSServiceFactory:
    def test_sarvam_tts_configuration_defaults(self):
        config = SarvamTTSConfiguration(api_key="test-key")

        assert config.provider == ServiceProviders.SARVAM
        assert config.model == "bulbul:v2"
        assert config.voice == "anushka"
        assert config.language == "hi-IN"
        assert config.speed == 1.0

    def test_sarvam_tts_voice_schema_allows_custom_model_specific_options(self):
        voice_schema = SarvamTTSConfiguration.model_json_schema()["properties"]["voice"]

        assert voice_schema["allow_custom_input"] is True
        assert "bulbul:v2" in voice_schema["model_options"]
        assert "bulbul:v3" in voice_schema["model_options"]

    def test_create_sarvam_tts_service_maps_speed_to_pace(self):
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                api_key="test-key",
                model="bulbul:v2",
                voice="anushka",
                language="hi-IN",
                speed=1.25,
            )
        )
        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamTTSService"
        ) as mock_service:
            create_tts_service(user_config, audio_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["settings"].model == "bulbul:v2"
        assert kwargs["settings"].voice == "anushka"
        assert kwargs["settings"].language == Language.HI
        assert kwargs["settings"].pace == 1.25

    def test_create_sarvam_tts_service_normalizes_custom_voice_id(self):
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                api_key="test-key",
                model="bulbul:v2",
                voice=" Rehan ",
                language="hi-IN",
                speed=1.0,
            )
        )
        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamTTSService"
        ) as mock_service:
            create_tts_service(user_config, audio_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].voice == "rehan"

    def test_create_sarvam_tts_service_defaults_blank_voice_id(self):
        user_config = SimpleNamespace(
            tts=SimpleNamespace(
                provider=ServiceProviders.SARVAM.value,
                api_key="test-key",
                model="bulbul:v2",
                voice="   ",
                language="hi-IN",
                speed=1.0,
            )
        )
        audio_config = AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        )

        with patch(
            "api.services.pipecat.service_factory.SarvamTTSService"
        ) as mock_service:
            create_tts_service(user_config, audio_config)

        kwargs = mock_service.call_args.kwargs
        assert kwargs["settings"].voice == "anushka"
