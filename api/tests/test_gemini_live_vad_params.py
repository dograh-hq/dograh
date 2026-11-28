from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from google.auth.credentials import AnonymousCredentials
from google.genai.types import EndSensitivity, StartSensitivity
from pydantic import ValidationError

from api.services.configuration import model_connections
from api.services.configuration.registry import (
    GoogleRealtimeLLMConfiguration,
    GoogleVertexRealtimeLLMConfiguration,
)
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.realtime.gemini_live_vertex import (
    DograhGeminiLiveVertexLLMService,
)
from api.services.pipecat.service_factory import create_realtime_llm_service

VAD_FIELDS = (
    "start_of_speech_sensitivity",
    "end_of_speech_sensitivity",
    "prefix_padding_ms",
    "silence_duration_ms",
)

GOOGLE_REQUIRED = {"api_key": "test-key"}
VERTEX_REQUIRED = {"project_id": "test-proj", "location": "us-central1"}


class DummyUserConfig:
    def __init__(self, realtime_config):
        self.realtime = realtime_config


def _audio_config():
    return SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )


@pytest.fixture
def anonymous_vertex_credentials(monkeypatch):
    # VAD plumbing is independent of deferred credential preparation.
    monkeypatch.setattr(
        DograhGeminiLiveVertexLLMService,
        "_get_credentials",
        staticmethod(lambda _credentials, _credentials_path: AnonymousCredentials()),
    )


@pytest.mark.parametrize(
    "config_cls",
    [GoogleRealtimeLLMConfiguration, GoogleVertexRealtimeLLMConfiguration],
)
def test_gemini_live_schema_exposes_vad_fields(config_cls):
    properties = config_cls.model_json_schema()["properties"]

    for name in VAD_FIELDS:
        assert name in properties, name
        assert properties[name]["default"] is None

    for name in ("start_of_speech_sensitivity", "end_of_speech_sensitivity"):
        assert properties[name]["examples"] == ["HIGH", "LOW"]
        enumerated = [
            option["enum"] for option in properties[name]["anyOf"] if "enum" in option
        ]
        assert enumerated == [["HIGH", "LOW"]]

    for name in ("prefix_padding_ms", "silence_duration_ms"):
        bounded = [
            option
            for option in properties[name]["anyOf"]
            if option.get("type") == "integer"
        ]
        assert bounded and bounded[0]["minimum"] == 0
        assert bounded[0]["maximum"] == 10000


@pytest.mark.parametrize(
    "config_cls, required_fields",
    [
        (GoogleRealtimeLLMConfiguration, GOOGLE_REQUIRED),
        (GoogleVertexRealtimeLLMConfiguration, VERTEX_REQUIRED),
    ],
)
def test_gemini_live_vad_fields_default_to_provider_defaults(
    config_cls, required_fields
):
    config = config_cls.model_validate(required_fields)

    for name in VAD_FIELDS:
        assert getattr(config, name) is None
    # Persisted configurations exclude None, so unset knobs leave no trace.
    assert not set(VAD_FIELDS) & set(config.model_dump(exclude_none=True))


@pytest.mark.parametrize(
    "config_cls, required_fields",
    [
        (GoogleRealtimeLLMConfiguration, GOOGLE_REQUIRED),
        (GoogleVertexRealtimeLLMConfiguration, VERTEX_REQUIRED),
    ],
)
@pytest.mark.parametrize(
    "invalid",
    [
        {"start_of_speech_sensitivity": "MEDIUM"},
        {"end_of_speech_sensitivity": "high"},
        {"prefix_padding_ms": -1},
        {"silence_duration_ms": 10001},
        {"silence_duration_ms": "soon"},
    ],
)
def test_gemini_live_vad_fields_reject_out_of_range_values(
    config_cls, required_fields, invalid
):
    with pytest.raises(ValidationError):
        config_cls.model_validate({**required_fields, **invalid})


def test_gemini_live_without_vad_fields_keeps_server_defaults():
    realtime_config = GoogleRealtimeLLMConfiguration(api_key="test-api-key")
    service = create_realtime_llm_service(
        DummyUserConfig(realtime_config), _audio_config()
    )

    assert isinstance(service, DograhGeminiLiveLLMService)
    assert service._settings.vad is None


def test_gemini_live_passes_vad_params_to_settings():
    realtime_config = GoogleRealtimeLLMConfiguration(
        api_key="test-api-key",
        model="gemini-live-2.5-flash",
        start_of_speech_sensitivity="HIGH",
        end_of_speech_sensitivity="HIGH",
        prefix_padding_ms=50,
        silence_duration_ms=400,
    )
    service = create_realtime_llm_service(
        DummyUserConfig(realtime_config), _audio_config()
    )

    vad = service._settings.vad
    assert vad is not None
    assert vad.disabled is None
    assert vad.start_sensitivity == StartSensitivity.START_SENSITIVITY_HIGH
    assert vad.end_sensitivity == EndSensitivity.END_SENSITIVITY_HIGH
    assert vad.prefix_padding_ms == 50
    assert vad.silence_duration_ms == 400


def test_gemini_live_partial_vad_params_leave_other_knobs_unset():
    realtime_config = GoogleRealtimeLLMConfiguration(
        api_key="test-api-key",
        silence_duration_ms=0,
    )
    service = create_realtime_llm_service(
        DummyUserConfig(realtime_config), _audio_config()
    )

    vad = service._settings.vad
    assert vad is not None
    assert vad.start_sensitivity is None
    assert vad.end_sensitivity is None
    assert vad.prefix_padding_ms is None
    # Zero is a real value, not "unset".
    assert vad.silence_duration_ms == 0


def test_gemini_vertex_without_vad_fields_keeps_server_defaults(
    anonymous_vertex_credentials,
):
    realtime_config = GoogleVertexRealtimeLLMConfiguration(**VERTEX_REQUIRED)
    service = create_realtime_llm_service(
        DummyUserConfig(realtime_config), _audio_config()
    )

    assert isinstance(service, DograhGeminiLiveVertexLLMService)
    assert service._settings.vad is None


def test_gemini_vertex_passes_vad_params_to_settings(anonymous_vertex_credentials):
    realtime_config = GoogleVertexRealtimeLLMConfiguration(
        **VERTEX_REQUIRED,
        model="google/gemini-live-2.5-flash",
        start_of_speech_sensitivity="LOW",
        end_of_speech_sensitivity="HIGH",
        prefix_padding_ms=20,
        silence_duration_ms=300,
    )
    service = create_realtime_llm_service(
        DummyUserConfig(realtime_config), _audio_config()
    )

    vad = service._settings.vad
    assert vad is not None
    assert vad.start_sensitivity == StartSensitivity.START_SENSITIVITY_LOW
    assert vad.end_sensitivity == EndSensitivity.END_SENSITIVITY_HIGH
    assert vad.prefix_padding_ms == 20
    assert vad.silence_duration_ms == 300


async def test_gemini_vertex_vad_params_survive_workflow_override(
    anonymous_vertex_credentials, monkeypatch
):
    # Go through the real override path: the organization default is patched
    # by a workflow override via resolve_model_configuration and
    # _apply_model_patch, and the effective realtime config feeds the factory.
    row = SimpleNamespace(
        uuid=str(uuid4()),
        provider="google_vertex",
        credentials={"api_key": "secret"},
        connection_settings={"project_id": "test-proj"},
        revision=1,
        organization_id=1,
        is_active=True,
    )
    default = SimpleNamespace(
        uuid=str(uuid4()),
        organization_id=1,
        revision=1,
        configuration={
            "version": 3,
            "mode": "realtime",
            "llm": {"provider_connection_uuid": row.uuid, "settings": {}},
            "realtime": {
                "provider_connection_uuid": row.uuid,
                "settings": {"start_of_speech_sensitivity": "LOW"},
            },
        },
    )

    async def get_connection(org, uuid, active_only=True):
        return row if org == row.organization_id and uuid == row.uuid else None

    monkeypatch.setattr(
        model_connections.db_client,
        "get_provider_connection",
        AsyncMock(side_effect=get_connection),
    )

    resolved = await model_connections.resolve_model_configuration(
        1,
        workflow_override={
            "realtime": {
                "settings": {
                    "end_of_speech_sensitivity": "HIGH",
                    "silence_duration_ms": 250,
                }
            }
        },
        default_row=default,
    )
    assert {"source": "workflow"} in resolved.provenance
    # The snapshot also stores every model default (model, voice, ...), so
    # only the saved and overridden VAD keys are checked here.
    snapshot_settings = resolved.snapshot["services"]["realtime"]["settings"]
    assert snapshot_settings["start_of_speech_sensitivity"] == "LOW"
    assert snapshot_settings["end_of_speech_sensitivity"] == "HIGH"
    assert snapshot_settings["silence_duration_ms"] == 250

    service = create_realtime_llm_service(resolved.effective, _audio_config())

    assert isinstance(service, DograhGeminiLiveVertexLLMService)
    vad = service._settings.vad
    assert vad is not None
    # The saved base setting is kept and the workflow settings are merged in.
    assert vad.start_sensitivity == StartSensitivity.START_SENSITIVITY_LOW
    assert vad.end_sensitivity == EndSensitivity.END_SENSITIVITY_HIGH
    assert vad.prefix_padding_ms is None
    assert vad.silence_duration_ms == 250
