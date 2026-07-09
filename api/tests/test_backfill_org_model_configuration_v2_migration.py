"""Tests for the 00b0201ad918 backfill migration's frozen conversion.

The migration writes MODEL_CONFIGURATION_V2 JSON directly, so its output must
keep parsing with OrganizationAIModelConfigurationV2 — these tests fail if the
schema drifts away from what the migration produces.
"""

import importlib.util
from pathlib import Path

from api.schemas.ai_model_configuration import (
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "00b0201ad918_backfill_org_model_configuration_v2.py"
)
_spec = importlib.util.spec_from_file_location(
    "backfill_org_model_configuration_v2", _MIGRATION_PATH
)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


def _parse_and_compile(value: dict) -> None:
    compile_ai_model_configuration_v2(
        OrganizationAIModelConfigurationV2.model_validate(value)
    )


def test_dograh_legacy_converts_to_managed_mode():
    value = migration.convert_legacy_configuration_to_v2(
        {
            "llm": {"provider": "dograh", "api_key": "mps-key", "model": "default"},
            "tts": {
                "provider": "dograh",
                "api_key": "mps-key",
                "voice": "aura",
                "speed": 1.2,
            },
            "stt": {"provider": "dograh", "api_key": "mps-key", "language": "en"},
        }
    )
    assert value["mode"] == "dograh"
    assert value["dograh"] == {
        "api_key": "mps-key",
        "voice": "aura",
        "speed": 1.2,
        "language": "en",
    }
    _parse_and_compile(value)


def test_dograh_mode_wins_over_byok_sections_and_sanitizes_speed():
    value = migration.convert_legacy_configuration_to_v2(
        {
            "llm": {"provider": "dograh", "api_key": ["mps-key"]},
            "tts": {"provider": "elevenlabs", "api_key": "el-key", "speed": 9.0},
            "stt": {"provider": "deepgram", "api_key": "dg-key"},
        }
    )
    assert value["mode"] == "dograh"
    assert value["dograh"]["api_key"] == "mps-key"
    assert value["dograh"]["speed"] == 1.0
    _parse_and_compile(value)


def test_byok_pipeline_legacy_converts_and_validates():
    value = migration.convert_legacy_configuration_to_v2(
        {
            "llm": {
                "provider": "openai",
                "api_key": "sk-test",
                "model": "gpt-4.1-mini",
                "temperature": 0.5,
            },
            "tts": {
                "provider": "elevenlabs",
                "api_key": "el-key",
                "model": "eleven_flash_v2_5",
                "voice": "voice-id",
            },
            "stt": {
                "provider": "deepgram",
                "api_key": "dg-key",
                "model": "nova-2-phonecall",
            },
        }
    )
    assert value["mode"] == "byok"
    assert value["byok"]["mode"] == "pipeline"
    assert "embeddings" not in value["byok"]["pipeline"]
    _parse_and_compile(value)


def test_realtime_legacy_converts_to_byok_realtime():
    value = migration.convert_legacy_configuration_to_v2(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "google_realtime",
                "api_key": "google-key",
                "model": "gemini-3.1-flash-live-preview",
                "voice": "Puck",
                "language": "en",
            },
            "llm": {
                "provider": "google",
                "api_key": "google-key",
                "model": "gemini-2.5-flash",
            },
        }
    )
    assert value["mode"] == "byok"
    assert value["byok"]["mode"] == "realtime"
    _parse_and_compile(value)


def test_incomplete_pipeline_legacy_is_skipped():
    assert (
        migration.convert_legacy_configuration_to_v2(
            {
                "llm": {"provider": "openai", "api_key": "sk-test"},
                "tts": {"provider": "elevenlabs", "api_key": "el-key"},
            }
        )
        is None
    )
    assert migration.convert_legacy_configuration_to_v2({}) is None


def test_dograh_provider_without_single_key_cannot_become_byok():
    # Multiple dograh keys can't map to managed mode, and BYOK rejects the
    # dograh provider — the org must be skipped rather than written broken.
    assert (
        migration.convert_legacy_configuration_to_v2(
            {
                "llm": {"provider": "dograh", "api_key": ["key-a", "key-b"]},
                "tts": {"provider": "elevenlabs", "api_key": "el-key"},
                "stt": {"provider": "deepgram", "api_key": "dg-key"},
            }
        )
        is None
    )
