"""The Deepgram TTS model backfill writes the family a stored voice names.

The migration works on raw JSON without importing application code, so these
tests load it from its file and then check its output against the registry,
which is what will read the rows afterwards.
"""

import importlib.util
from pathlib import Path

import pytest

from api.services.configuration.registry import DeepgramTTSConfiguration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "c7bc91540b31_backfill_deepgram_tts_model.py"
)
_spec = importlib.util.spec_from_file_location(
    "backfill_deepgram_tts_model", _MIGRATION_PATH
)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

DEEPGRAM = "fbc31e0d-f9b5-5be2-9d54-69a0576b8ba1"
OTHER = "4c085109-dd81-46e9-92bc-9eb0e864fd80"
UUIDS = {DEEPGRAM}


def _configuration(voice, uuid=DEEPGRAM, **settings):
    return {
        "version": 3,
        "mode": "pipeline",
        "llm": {"provider_connection_uuid": uuid, "settings": {}},
        "tts": {
            "provider_connection_uuid": uuid,
            "settings": {"voice": voice, **settings},
        },
    }


def _workflow(voice, uuid=DEEPGRAM):
    return {
        "max_call_duration": 300,
        migration.BINDING_KEY: {
            "mode": "pipeline",
            "tts": {"provider_connection_uuid": uuid, "settings": {"voice": voice}},
        },
    }


@pytest.mark.parametrize(
    "voice,model",
    [
        ("flux-maeve-en", "flux"),
        ("aura-2-leda-nl", "aura-2"),
        ("aura-asteria-en", "aura-1"),
        ("my-cloned-voice", "aura-2"),
        (None, "aura-2"),
    ],
)
def test_named_configuration_gets_the_family_its_voice_names(voice, model):
    payload = _configuration(voice)

    assert migration.add_model(payload, (), UUIDS) is True
    assert payload["tts"]["settings"] == {"voice": voice, "model": model}
    assert payload["llm"]["settings"] == {}

    # The registry is what reads the row next; the pair it gets must agree.
    settings = {k: v for k, v in payload["tts"]["settings"].items() if v is not None}
    config = DeepgramTTSConfiguration(api_key="test-key", **settings)
    assert config.model == model


def test_other_settings_survive_the_backfill():
    payload = _configuration("aura-2-estrella-es", speed=1.2)

    assert migration.add_model(payload, (), UUIDS) is True
    assert payload["tts"]["settings"] == {
        "voice": "aura-2-estrella-es",
        "speed": 1.2,
        "model": "aura-2",
    }
    assert (
        DeepgramTTSConfiguration(api_key="k", **payload["tts"]["settings"]).speed == 1.2
    )


def test_workflow_and_definition_overrides_are_reached_through_the_binding():
    payload = _workflow("aura-asteria-en")

    assert migration.add_model(payload, (migration.BINDING_KEY,), UUIDS) is True
    assert payload[migration.BINDING_KEY]["tts"]["settings"]["model"] == "aura-1"
    assert payload["max_call_duration"] == 300


@pytest.mark.parametrize(
    "payload,path",
    [
        # A binding to a named configuration has no inline TTS block.
        (
            {migration.BINDING_KEY: {"model_configuration_uuid": DEEPGRAM}},
            (migration.BINDING_KEY,),
        ),
        # Workflows that inherit the organization default carry no binding.
        ({"max_call_duration": 300}, (migration.BINDING_KEY,)),
        # Another provider's voice is not Deepgram's business.
        (_configuration("79d2cf27-444a-4c3a-9eed-2ad5cf795a3b", uuid=OTHER), ()),
        # Already migrated, or saved by the new form.
        (_configuration("flux-maeve-en", model="flux"), ()),
    ],
)
def test_rows_without_a_deepgram_tts_block_to_complete_are_left_alone(payload, path):
    before = repr(payload)

    assert migration.add_model(payload, path, UUIDS) is False
    assert repr(payload) == before


def test_downgrade_removes_exactly_what_upgrade_added():
    payload = _configuration("aura-2-leda-nl", speed=1.0)
    original = repr(payload)

    assert migration.add_model(payload, (), UUIDS) is True
    assert migration.remove_model(payload, (), UUIDS) is True
    assert repr(payload) == original
    assert migration.remove_model(payload, (), UUIDS) is False
