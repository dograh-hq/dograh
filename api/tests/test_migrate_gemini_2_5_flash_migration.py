"""Tests for the b41f7c9d2e05 gemini-2.5-flash migration's model walker.

The walker runs over three different stored shapes (org/user v1, workflow v2
override, legacy model_overrides), so these tests pin the shapes it must handle
and the providers it must leave alone.
"""

import importlib.util
from pathlib import Path

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "b41f7c9d2e05_migrate_gemini_2_5_flash_to_3_5_flash.py"
)
_spec = importlib.util.spec_from_file_location(
    "migrate_gemini_2_5_flash_to_3_5_flash", _MIGRATION_PATH
)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

MAPPING = migration._MODEL_MIGRATIONS


def _migrate(payload):
    changed = migration._migrate_models(payload, MAPPING)
    return payload, changed


def test_legacy_v1_user_configuration_llm_is_migrated():
    payload, changed = _migrate(
        {
            "llm": {"provider": "google", "model": "gemini-2.5-flash", "api_key": "k"},
            "stt": {"provider": "dograh", "model": "default"},
            "tts": {"provider": "dograh", "model": "default", "voice": "default"},
        }
    )
    assert changed is True
    assert payload["llm"]["model"] == "gemini-3.5-flash"
    assert payload["llm"]["api_key"] == "k"
    assert payload["stt"]["model"] == "default"


def test_workflow_v2_override_pipeline_and_realtime_are_migrated():
    payload, changed = _migrate(
        {
            "max_call_duration": 600,
            "model_configuration_v2_override": {
                "version": 2,
                "mode": "byok",
                "byok": {
                    "mode": "realtime",
                    "realtime": {
                        "realtime": {
                            "provider": "google_realtime",
                            "model": "gemini-3.1-flash-live-preview",
                        },
                        "llm": {
                            "provider": "google",
                            "model": "gemini-2.5-flash-lite",
                        },
                    },
                },
            },
        }
    )
    assert changed is True
    realtime = payload["model_configuration_v2_override"]["byok"]["realtime"]
    assert realtime["llm"]["model"] == "gemini-3.5-flash-lite"
    assert realtime["realtime"]["model"] == "gemini-3.1-flash-live-preview"
    assert payload["max_call_duration"] == 600


def test_legacy_model_overrides_block_is_migrated():
    payload, changed = _migrate(
        {
            "model_overrides": {
                "llm": {"model": "gemini-2.5-flash", "provider": "google"}
            }
        }
    )
    assert changed is True
    assert payload["model_overrides"]["llm"]["model"] == "gemini-3.5-flash"


def test_google_vertex_is_migrated():
    payload, changed = _migrate(
        {
            "llm": {
                "provider": "google_vertex",
                "model": "gemini-2.5-flash",
                "project_id": "p",
            }
        }
    )
    assert changed is True
    assert payload["llm"]["model"] == "gemini-3.5-flash"


def test_openrouter_slug_is_left_alone():
    payload, changed = _migrate(
        {"llm": {"provider": "openrouter", "model": "google/gemini-2.5-flash"}}
    )
    assert changed is False
    assert payload["llm"]["model"] == "google/gemini-2.5-flash"


def test_non_google_provider_with_same_model_id_is_left_alone():
    payload, changed = _migrate(
        {"llm": {"provider": "speaches", "model": "gemini-2.5-flash"}}
    )
    assert changed is False
    assert payload["llm"]["model"] == "gemini-2.5-flash"


def test_native_audio_and_live_variants_are_left_alone():
    payload, changed = _migrate(
        {
            "realtime": {
                "provider": "google",
                "model": "gemini-2.5-flash-native-audio-preview-12-2025",
            },
            "llm": {
                "provider": "google_vertex",
                "model": "google/gemini-live-2.5-flash-native-audio",
            },
        }
    )
    assert changed is False


def test_already_migrated_payload_reports_no_change():
    payload, changed = _migrate(
        {"llm": {"provider": "google", "model": "gemini-3.5-flash"}}
    )
    assert changed is False


def test_walker_descends_through_lists():
    payload, changed = _migrate(
        {"services": [{"provider": "google", "model": "gemini-2.5-flash"}]}
    )
    assert changed is True
    assert payload["services"][0]["model"] == "gemini-3.5-flash"
