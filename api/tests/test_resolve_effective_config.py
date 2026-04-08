"""
TDD tests for resolve_effective_config().

This function deep-merges workflow-level model_overrides onto the global
UserConfiguration. Fields not overridden inherit from global.

Module under test: api.services.configuration.resolve
"""

import pytest

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    ElevenlabsTTSConfiguration,
    GoogleRealtimeLLMConfiguration,
    OpenAILLMService,
)
from api.services.configuration.resolve import resolve_effective_config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def global_config() -> UserConfiguration:
    """A realistic global user configuration."""
    return UserConfiguration(
        llm=OpenAILLMService(
            provider="openai", api_key="sk-global-llm", model="gpt-4.1"
        ),
        tts=ElevenlabsTTSConfiguration(
            provider="elevenlabs",
            api_key="el-global-tts",
            voice="Rachel",
            model="eleven_flash_v2_5",
        ),
        stt=DeepgramSTTConfiguration(
            provider="deepgram",
            api_key="dg-global-stt",
            model="nova-3-general",
            language="multi",
        ),
        is_realtime=False,
        realtime=None,
    )


@pytest.fixture
def global_config_realtime() -> UserConfiguration:
    """Global config with realtime enabled."""
    return UserConfiguration(
        llm=OpenAILLMService(
            provider="openai", api_key="sk-global-llm", model="gpt-4.1"
        ),
        tts=ElevenlabsTTSConfiguration(
            provider="elevenlabs",
            api_key="el-global-tts",
            voice="Rachel",
            model="eleven_flash_v2_5",
        ),
        stt=DeepgramSTTConfiguration(
            provider="deepgram",
            api_key="dg-global-stt",
            model="nova-3-general",
            language="multi",
        ),
        is_realtime=True,
        realtime=GoogleRealtimeLLMConfiguration(
            provider="google_realtime",
            api_key="goog-global-rt",
            model="gemini-3.1-flash-live-preview",
            voice="Puck",
            language="en",
        ),
    )


# ---------------------------------------------------------------------------
# No overrides → global returned unchanged
# ---------------------------------------------------------------------------


class TestNoOverrides:
    def test_none_overrides_returns_global(self, global_config):
        result = resolve_effective_config(global_config, None)
        assert result.llm.model == "gpt-4.1"
        assert result.tts.voice == "Rachel"
        assert result.stt.model == "nova-3-general"
        assert result.is_realtime is False

    def test_empty_dict_overrides_returns_global(self, global_config):
        result = resolve_effective_config(global_config, {})
        assert result.llm.model == "gpt-4.1"
        assert result.tts.voice == "Rachel"

    def test_does_not_mutate_original(self, global_config):
        """The original config object must not be modified."""
        resolve_effective_config(global_config, {"llm": {"model": "gpt-4.1-mini"}})
        assert global_config.llm.model == "gpt-4.1"


# ---------------------------------------------------------------------------
# Single-field overrides within a section (same provider)
# ---------------------------------------------------------------------------


class TestSingleFieldOverride:
    def test_override_llm_model_only(self, global_config):
        result = resolve_effective_config(
            global_config, {"llm": {"model": "gpt-4.1-mini"}}
        )
        assert result.llm.model == "gpt-4.1-mini"
        assert result.llm.provider == "openai"  # inherited
        assert result.llm.api_key == "sk-global-llm"  # inherited

    def test_override_tts_voice_only(self, global_config):
        result = resolve_effective_config(global_config, {"tts": {"voice": "shimmer"}})
        assert result.tts.voice == "shimmer"
        assert result.tts.provider == "elevenlabs"  # inherited
        assert result.tts.api_key == "el-global-tts"  # inherited

    def test_override_stt_language_only(self, global_config):
        result = resolve_effective_config(global_config, {"stt": {"language": "en"}})
        assert result.stt.language == "en"
        assert result.stt.model == "nova-3-general"  # inherited
        assert result.stt.provider == "deepgram"  # inherited


# ---------------------------------------------------------------------------
# Provider change (requires full section replacement)
# ---------------------------------------------------------------------------


class TestProviderChange:
    def test_override_llm_to_different_provider(self, global_config):
        result = resolve_effective_config(
            global_config,
            {
                "llm": {
                    "provider": "groq",
                    "api_key": "groq-key",
                    "model": "llama-3.3-70b-versatile",
                }
            },
        )
        assert result.llm.provider == "groq"
        assert result.llm.model == "llama-3.3-70b-versatile"
        assert result.llm.api_key == "groq-key"

    def test_provider_change_does_not_affect_other_sections(self, global_config):
        result = resolve_effective_config(
            global_config,
            {
                "llm": {
                    "provider": "groq",
                    "api_key": "groq-key",
                    "model": "llama-3.3-70b-versatile",
                }
            },
        )
        # TTS and STT unchanged
        assert result.tts.provider == "elevenlabs"
        assert result.stt.provider == "deepgram"


# ---------------------------------------------------------------------------
# API key inheritance
# ---------------------------------------------------------------------------


class TestAPIKeyInheritance:
    def test_no_api_key_in_override_inherits_global(self, global_config):
        """When override omits api_key, global key is used."""
        result = resolve_effective_config(
            global_config, {"llm": {"model": "gpt-4.1-mini"}}
        )
        assert result.llm.api_key == "sk-global-llm"

    def test_explicit_api_key_in_override_wins(self, global_config):
        """When override includes api_key, it takes precedence."""
        result = resolve_effective_config(
            global_config,
            {"llm": {"model": "gpt-4.1-mini", "api_key": "sk-override-key"}},
        )
        assert result.llm.api_key == "sk-override-key"


# ---------------------------------------------------------------------------
# is_realtime override
# ---------------------------------------------------------------------------


class TestRealtimeOverride:
    def test_enable_realtime_on_non_realtime_global(self, global_config):
        result = resolve_effective_config(
            global_config,
            {
                "is_realtime": True,
                "realtime": {
                    "provider": "google_realtime",
                    "api_key": "goog-override",
                    "model": "gemini-3.1-flash-live-preview",
                    "voice": "Charon",
                    "language": "en",
                },
            },
        )
        assert result.is_realtime is True
        assert result.realtime.provider == "google_realtime"
        assert result.realtime.voice == "Charon"

    def test_disable_realtime_on_realtime_global(self, global_config_realtime):
        result = resolve_effective_config(
            global_config_realtime, {"is_realtime": False}
        )
        assert result.is_realtime is False
        # Realtime config may still be present but is_realtime flag controls usage

    def test_override_realtime_voice_only(self, global_config_realtime):
        result = resolve_effective_config(
            global_config_realtime, {"realtime": {"voice": "Kore"}}
        )
        assert result.realtime.voice == "Kore"
        assert result.realtime.provider == "google_realtime"  # inherited
        assert result.realtime.api_key == "goog-global-rt"  # inherited

    def test_override_is_realtime_only_without_realtime_section(self, global_config):
        """Override is_realtime=True but provide no realtime config.
        Should set the flag; realtime section stays None from global."""
        result = resolve_effective_config(global_config, {"is_realtime": True})
        assert result.is_realtime is True
        assert result.realtime is None  # no config provided


# ---------------------------------------------------------------------------
# Section override when global has None for that section
# ---------------------------------------------------------------------------


class TestOverrideOnNullGlobal:
    def test_override_stt_when_global_is_none(self):
        """When global has no STT config, override creates one from scratch."""
        config = UserConfiguration(
            llm=OpenAILLMService(provider="openai", api_key="sk-key", model="gpt-4.1"),
            stt=None,
            tts=None,
            is_realtime=False,
        )
        result = resolve_effective_config(
            config,
            {
                "stt": {
                    "provider": "deepgram",
                    "api_key": "dg-new",
                    "model": "nova-3-general",
                    "language": "en",
                }
            },
        )
        assert result.stt is not None
        assert result.stt.provider == "deepgram"
        assert result.stt.model == "nova-3-general"

    def test_override_realtime_when_global_is_none(self):
        """Realtime section can be created from override even if global has none."""
        config = UserConfiguration(
            llm=OpenAILLMService(provider="openai", api_key="sk-key", model="gpt-4.1"),
            is_realtime=False,
            realtime=None,
        )
        result = resolve_effective_config(
            config,
            {
                "is_realtime": True,
                "realtime": {
                    "provider": "google_realtime",
                    "api_key": "goog-new",
                    "model": "gemini-3.1-flash-live-preview",
                    "voice": "Puck",
                    "language": "en",
                },
            },
        )
        assert result.is_realtime is True
        assert result.realtime.provider == "google_realtime"


# ---------------------------------------------------------------------------
# Multi-section overrides
# ---------------------------------------------------------------------------


class TestMultiSectionOverride:
    def test_override_llm_and_tts_not_stt(self, global_config):
        result = resolve_effective_config(
            global_config,
            {
                "llm": {"model": "gpt-4.1-mini"},
                "tts": {"voice": "shimmer"},
            },
        )
        assert result.llm.model == "gpt-4.1-mini"
        assert result.tts.voice == "shimmer"
        # STT untouched
        assert result.stt.model == "nova-3-general"
        assert result.stt.language == "multi"

    def test_override_all_sections(self, global_config):
        result = resolve_effective_config(
            global_config,
            {
                "llm": {"model": "gpt-4.1-mini"},
                "tts": {"voice": "shimmer"},
                "stt": {"language": "en"},
                "is_realtime": True,
                "realtime": {
                    "provider": "google_realtime",
                    "api_key": "goog-key",
                    "model": "gemini-3.1-flash-live-preview",
                    "voice": "Fenrir",
                    "language": "en",
                },
            },
        )
        assert result.llm.model == "gpt-4.1-mini"
        assert result.tts.voice == "shimmer"
        assert result.stt.language == "en"
        assert result.is_realtime is True
        assert result.realtime.voice == "Fenrir"


# ---------------------------------------------------------------------------
# Ignored / unknown keys
# ---------------------------------------------------------------------------


class TestUnknownKeys:
    def test_unknown_section_in_overrides_is_ignored(self, global_config):
        """Override with a key that doesn't map to any section should not crash."""
        result = resolve_effective_config(
            global_config, {"unknown_section": {"foo": "bar"}}
        )
        assert result.llm.model == "gpt-4.1"

    def test_embeddings_not_overridable(self, global_config):
        """Embeddings stay global — overrides for embeddings should be ignored."""
        result = resolve_effective_config(
            global_config,
            {"embeddings": {"provider": "openai", "model": "text-embedding-3-small"}},
        )
        assert result.embeddings is None  # was None in global, stays None
