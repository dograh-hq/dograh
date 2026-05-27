"""
TDD tests for resolve_effective_config().

This function deep-merges workflow-level model_overrides onto the global
UserConfiguration. Fields not overridden inherit from global.

Module under test: api.services.configuration.resolve
"""

import pytest

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.masking import (
    contains_masked_key,
    mask_workflow_configurations,
)
from api.services.configuration.merge import merge_workflow_configuration_secrets
from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    ElevenlabsTTSConfiguration,
    GoogleRealtimeLLMConfiguration,
    GoogleVertexLLMConfiguration,
    GrokRealtimeLLMConfiguration,
    OpenAILLMService,
    UltravoxRealtimeLLMConfiguration,
)
from api.services.configuration.resolve import (
    enrich_overrides_with_api_keys,
    resolve_effective_config,
)

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

    def test_override_llm_to_google_vertex(self, global_config):
        result = resolve_effective_config(
            global_config,
            {
                "llm": {
                    "provider": "google_vertex",
                    "model": "gemini-2.5-flash",
                    "project_id": "demo-project",
                    "location": "us-east4",
                    "credentials": '{"type":"service_account"}',
                }
            },
        )
        assert isinstance(result.llm, GoogleVertexLLMConfiguration)
        assert result.llm.provider == "google_vertex"
        assert result.llm.project_id == "demo-project"


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

    def test_switch_realtime_provider_to_grok(self, global_config_realtime):
        result = resolve_effective_config(
            global_config_realtime,
            {
                "realtime": {
                    "provider": "grok_realtime",
                    "api_key": "xai-key",
                    "model": "grok-voice-think-fast-1.0",
                    "voice": "Sal",
                }
            },
        )
        assert isinstance(result.realtime, GrokRealtimeLLMConfiguration)
        assert result.realtime.provider == "grok_realtime"
        assert result.realtime.voice == "Sal"

    def test_switch_realtime_provider_to_ultravox(self, global_config_realtime):
        result = resolve_effective_config(
            global_config_realtime,
            {
                "realtime": {
                    "provider": "ultravox_realtime",
                    "api_key": "ultra-key",
                    "model": "ultravox-v0.7",
                    "voice": "Mark",
                }
            },
        )
        assert isinstance(result.realtime, UltravoxRealtimeLLMConfiguration)
        assert result.realtime.provider == "ultravox_realtime"
        assert result.realtime.voice == "Mark"

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


# ---------------------------------------------------------------------------
# enrich_overrides_with_api_keys
# ---------------------------------------------------------------------------


class TestEnrichOverridesWithApiKeys:
    def test_injects_api_key_when_same_provider(self, global_config):
        """Override matching the global provider gets the global API key stamped in."""
        overrides = {
            "tts": {
                "provider": "elevenlabs",
                "voice": "Bella",
                "model": "eleven_flash_v2_5",
            }
        }
        enriched = enrich_overrides_with_api_keys(overrides, global_config)
        assert enriched["tts"]["api_key"] == "el-global-tts"

    def test_injects_all_api_keys_when_global_has_multiple(self, global_config):
        """Override matching a multi-key global provider gets every global key."""
        global_config.llm.api_key = ["sk-global-1", "sk-global-2"]
        overrides = {"llm": {"provider": "openai", "model": "gpt-4.1-mini"}}

        enriched = enrich_overrides_with_api_keys(overrides, global_config)

        assert enriched["llm"]["api_key"] == ["sk-global-1", "sk-global-2"]

    def test_does_not_overwrite_existing_api_key(self, global_config):
        """Override that already has an api_key keeps its own key."""
        overrides = {
            "tts": {
                "provider": "elevenlabs",
                "api_key": "my-own-key",
                "voice": "Bella",
                "model": "eleven_flash_v2_5",
            }
        }
        enriched = enrich_overrides_with_api_keys(overrides, global_config)
        assert enriched["tts"]["api_key"] == "my-own-key"

    def test_skips_when_provider_differs(self, global_config):
        """Override for a different provider is not enriched with the global key."""
        overrides = {
            "tts": {"provider": "cartesia", "voice": "some-voice", "model": "sonic-3"}
        }
        enriched = enrich_overrides_with_api_keys(overrides, global_config)
        assert "api_key" not in enriched["tts"]

    def test_does_not_mutate_original(self, global_config):
        """The input overrides dict must not be modified."""
        overrides = {
            "tts": {
                "provider": "elevenlabs",
                "voice": "Bella",
                "model": "eleven_flash_v2_5",
            }
        }
        original_copy = {
            "tts": {
                "provider": "elevenlabs",
                "voice": "Bella",
                "model": "eleven_flash_v2_5",
            }
        }
        enrich_overrides_with_api_keys(overrides, global_config)
        assert overrides == original_copy

    def test_regression_override_survives_global_provider_change(self, global_config):
        """Core bug: override for provider A still works after global switches to B.

        Steps:
          1. Global TTS = ElevenLabs, Override TTS = ElevenLabs (different voice)
          2. enrich_overrides_with_api_keys stamps ElevenLabs API key into override
          3. Global TTS changes to Deepgram (simulate by building a new config)
          4. resolve_effective_config must still return a valid ElevenLabs config
        """
        override_at_save_time = {
            "tts": {
                "provider": "elevenlabs",
                "voice": "Bella",
                "model": "eleven_flash_v2_5",
            }
        }
        enriched = enrich_overrides_with_api_keys(override_at_save_time, global_config)
        assert enriched["tts"]["api_key"] == "el-global-tts"

        # Simulate global config switching to Deepgram
        from api.services.configuration.registry import DeepgramTTSConfiguration

        new_global = global_config.model_copy(
            update={
                "tts": DeepgramTTSConfiguration(
                    provider="deepgram", api_key="dg-new", voice="aura-2-helena-en"
                )
            }
        )

        # The enriched override should resolve correctly against the new global
        result = resolve_effective_config(new_global, enriched)
        assert result.tts.provider == "elevenlabs"
        assert result.tts.voice == "Bella"
        assert result.tts.api_key == "el-global-tts"


class TestWorkflowConfigurationSecrets:
    def test_masks_model_override_secrets(self):
        configs = {
            "model_overrides": {
                "llm": {
                    "provider": "openai",
                    "api_key": "sk-real-llm-key",
                    "model": "gpt-4.1-mini",
                },
                "tts": {
                    "provider": "elevenlabs",
                    "api_key": "el-real-tts-key",
                    "voice": "Bella",
                },
            },
            "ambient_noise_configuration": {"enabled": True},
        }

        masked = mask_workflow_configurations(configs)

        assert masked["model_overrides"]["llm"]["api_key"] != "sk-real-llm-key"
        assert contains_masked_key(masked["model_overrides"]["llm"]["api_key"])
        assert masked["model_overrides"]["llm"]["api_key"].endswith("-key")
        assert masked["model_overrides"]["tts"]["api_key"] != "el-real-tts-key"
        assert masked["ambient_noise_configuration"] == {"enabled": True}
        assert configs["model_overrides"]["llm"]["api_key"] == "sk-real-llm-key"

    def test_restores_masked_model_override_secrets_from_existing_config(self):
        existing = {
            "model_overrides": {
                "tts": {
                    "provider": "elevenlabs",
                    "api_key": "el-real-tts-key",
                    "voice": "Rachel",
                }
            }
        }
        incoming = mask_workflow_configurations(existing)
        incoming["model_overrides"]["tts"]["voice"] = "Bella"

        merged = merge_workflow_configuration_secrets(incoming, existing)

        assert merged["model_overrides"]["tts"]["api_key"] == "el-real-tts-key"
        assert merged["model_overrides"]["tts"]["voice"] == "Bella"
        assert incoming["model_overrides"]["tts"]["api_key"] != "el-real-tts-key"

    def test_single_masked_key_preserves_existing_multi_key_override(self):
        existing = {
            "model_overrides": {
                "llm": {
                    "provider": "openai",
                    "api_key": ["sk-workflow-1", "sk-workflow-2"],
                    "model": "gpt-4.1-mini",
                }
            }
        }
        incoming = mask_workflow_configurations(existing)
        incoming["model_overrides"]["llm"]["api_key"] = incoming["model_overrides"][
            "llm"
        ]["api_key"][0]

        merged = merge_workflow_configuration_secrets(incoming, existing)

        assert merged["model_overrides"]["llm"]["api_key"] == [
            "sk-workflow-1",
            "sk-workflow-2",
        ]

    def test_missing_secret_copies_current_global_key_instead_of_existing_workflow_key(
        self, global_config
    ):
        global_config.stt.api_key = ["dg-global-1", "dg-global-2"]
        existing = {
            "model_overrides": {
                "stt": {
                    "provider": "deepgram",
                    "api_key": "dg-workflow-key",
                    "model": "nova-3-general",
                    "language": "multi",
                }
            }
        }
        incoming = {
            "model_overrides": {
                "stt": {
                    "provider": "deepgram",
                    "model": "nova-3-general",
                    "language": "en",
                }
            }
        }

        merged = merge_workflow_configuration_secrets(incoming, existing)
        enriched = enrich_overrides_with_api_keys(
            merged["model_overrides"],
            global_config,
        )

        assert enriched["stt"]["api_key"] == ["dg-global-1", "dg-global-2"]
        assert enriched["stt"]["language"] == "en"
