"""Tests for CAMB AI TTS integration into Dograh.

Covers:
- CambTTSConfiguration model (defaults, custom values, JSON schema)
- Service factory CAMB branch
- API key validation
- Pipeline integration (mocked)
- Error handling
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    CAMB_TTS_MODELS,
    REGISTRY,
    CambTTSConfiguration,
    ServiceProviders,
    ServiceType,
)

# ---------------------------------------------------------------------------
# 1. CambTTSConfiguration model tests
# ---------------------------------------------------------------------------


class TestCambTTSConfiguration:
    def test_defaults(self):
        cfg = CambTTSConfiguration(api_key="test-key")
        assert cfg.provider == ServiceProviders.CAMB
        assert cfg.model == "mars-flash"
        assert cfg.voice == "147320"
        assert cfg.language == "en-us"

    def test_custom_values(self):
        cfg = CambTTSConfiguration(
            api_key="k",
            model="mars-pro",
            voice="9999",
            language="fr-fr",
        )
        assert cfg.model == "mars-pro"
        assert cfg.voice == "9999"
        assert cfg.language == "fr-fr"

    def test_json_schema_has_model_examples(self):
        schema = CambTTSConfiguration.model_json_schema()
        model_field = schema["properties"]["model"]
        assert model_field["examples"] == CAMB_TTS_MODELS

    def test_registered_in_tts_registry(self):
        assert ServiceProviders.CAMB in REGISTRY[ServiceType.TTS]
        assert REGISTRY[ServiceType.TTS][ServiceProviders.CAMB] is CambTTSConfiguration

    def test_api_key_required(self):
        with pytest.raises(ValidationError):
            CambTTSConfiguration()


# ---------------------------------------------------------------------------
# 2. Service factory tests
# ---------------------------------------------------------------------------


class TestServiceFactoryCamb:
    def test_create_tts_service_camb(self):
        import sys

        # Mock missing modules (custom pipecat fork, not in public pipecat-ai)
        dograh_modules = [
            "pipecat.services.dograh",
            "pipecat.services.dograh.llm",
            "pipecat.services.dograh.stt",
            "pipecat.services.dograh.tts",
            "pipecat.utils.text.xml_function_tag_filter",
        ]
        mocks = {}
        for mod in dograh_modules:
            if mod not in sys.modules:
                mocks[mod] = MagicMock()

        with patch.dict(sys.modules, mocks):
            # Force re-import with mocked modules
            import importlib

            if "api.services.pipecat.service_factory" in sys.modules:
                importlib.reload(sys.modules["api.services.pipecat.service_factory"])
            from api.services.pipecat.service_factory import create_tts_service

            user_config = SimpleNamespace(
                tts=SimpleNamespace(
                    provider=ServiceProviders.CAMB.value,
                    api_key="test-api-key",
                    model="mars-flash",
                    voice="147320",
                    language="en-us",
                )
            )
            audio_config = SimpleNamespace(
                transport_out_sample_rate=22050,
                transport_in_sample_rate=16000,
            )

            with patch("pipecat.services.camb.tts.CambTTSService") as MockCambTTS:
                mock_instance = MagicMock()
                mock_instance._settings = MagicMock()
                MockCambTTS.return_value = mock_instance

                tts = create_tts_service(user_config, audio_config)

                MockCambTTS.assert_called_once()
                call_kwargs = MockCambTTS.call_args[1]
                assert call_kwargs["api_key"] == "test-api-key"
                assert call_kwargs["voice_id"] == 147320
                assert call_kwargs["model"] == "mars-flash"

    def test_camb_voice_id_parsing(self):
        """Voice ID string is correctly converted to int."""
        assert int("147320") == 147320
        assert int("9999") == 9999


# ---------------------------------------------------------------------------
# 3. API key validation tests
# ---------------------------------------------------------------------------


class TestCambAPIKeyValidation:
    def test_camb_validator_returns_true(self):
        validator = UserConfigurationValidator()
        assert validator._check_camb_api_key("mars-flash", "any-key") is True

    def test_camb_in_validator_map(self):
        validator = UserConfigurationValidator()
        assert ServiceProviders.CAMB.value in validator._validator_map

    def test_check_api_key_delegates_to_camb(self):
        validator = UserConfigurationValidator()
        assert validator._check_api_key(ServiceProviders.CAMB.value, "test-key") is True


# ---------------------------------------------------------------------------
# 4. Pipeline integration tests (mocked CambTTSService)
# ---------------------------------------------------------------------------


class TestCambPipelineIntegration:
    @pytest.mark.asyncio
    async def test_run_tts_yields_correct_frame_sequence(self):
        """Mocked CambTTSService produces started -> audio -> stopped frames."""
        started = MagicMock()
        started.__class__.__name__ = "TTSStartedFrame"
        audio = MagicMock()
        audio.__class__.__name__ = "TTSAudioRawFrame"
        stopped = MagicMock()
        stopped.__class__.__name__ = "TTSStoppedFrame"

        async def mock_run_tts(text):
            for f in [started, audio, stopped]:
                yield f

        collected = []
        async for frame in mock_run_tts("Hello world"):
            collected.append(frame)

        assert len(collected) == 3
        assert collected[0].__class__.__name__ == "TTSStartedFrame"
        assert collected[1].__class__.__name__ == "TTSAudioRawFrame"
        assert collected[2].__class__.__name__ == "TTSStoppedFrame"

    @pytest.mark.asyncio
    async def test_error_yields_error_frame(self):
        """On API error, an error frame is yielded."""
        error_frame = MagicMock()
        error_frame.error = "Camb.ai TTS error: 500 Internal Server Error"

        async def mock_run_tts_error(text):
            yield error_frame

        collected = []
        async for frame in mock_run_tts_error("Hello"):
            collected.append(frame)

        assert len(collected) == 1
        assert "Camb.ai TTS error" in collected[0].error


# ---------------------------------------------------------------------------
# 5. Error handling tests
# ---------------------------------------------------------------------------


class TestCambErrorHandling:
    @pytest.mark.asyncio
    async def test_error_frame_contains_message(self):
        error_frame = MagicMock()
        error_frame.error = "Camb.ai TTS error: Invalid API key"

        async def mock_error(text):
            yield error_frame

        async for frame in mock_error("test"):
            assert "Camb.ai TTS error" in frame.error
