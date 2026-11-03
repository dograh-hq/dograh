"""Where Vertex requests are processed is decided by the location we pass.

Vertex builds its endpoint from the location, and only the regional and
multi-region endpoints keep processing inside a geography. A fallback that
differs from the configured default therefore sends an operator who never
touched the field somewhere they did not choose and cannot see, so these tests
pin the default, the pass-through, and the fact that both live in one constant.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.configuration.options import (
    GOOGLE_VERTEX_DEFAULT_LOCATION,
    GOOGLE_VERTEX_LOCATIONS,
)
from api.services.configuration.registry import (
    GoogleVertexLLMConfiguration,
    GoogleVertexRealtimeLLMConfiguration,
    ServiceProviders,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_llm_service_from_provider,
    create_realtime_llm_service,
)


def _audio_config() -> AudioConfig:
    return AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )


def _build_llm(location):
    with patch(
        "api.services.pipecat.service_factory.DograhGoogleVertexLLMService"
    ) as mock:
        create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE_VERTEX.value,
            model="gemini-3.6-flash",
            api_key=None,
            project_id="test-project",
            location=location,
            credentials=None,
        )
    mock.assert_called_once()
    return mock.call_args.kwargs["location"]


def _build_realtime(location):
    user_config = SimpleNamespace(
        realtime=SimpleNamespace(
            provider=ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
            model="google/gemini-live-2.5-flash-native-audio",
            api_key=None,
            voice="Charon",
            language="it",
            project_id="test-project",
            location=location,
            credentials=None,
        )
    )
    target = (
        "api.services.pipecat.realtime.gemini_live_vertex."
        "DograhGeminiLiveVertexLLMService"
    )
    with patch(target) as mock:
        create_realtime_llm_service(user_config, _audio_config())
    mock.assert_called_once()
    return mock.call_args.kwargs["location"]


@pytest.mark.parametrize("build", [_build_llm, _build_realtime])
@pytest.mark.parametrize("configured", ["eu", "us", "europe-west4"])
def test_configured_location_reaches_the_service(build, configured):
    assert build(configured) == configured


@pytest.mark.parametrize("build", [_build_llm, _build_realtime])
@pytest.mark.parametrize("unset", [None, "", "   "])
def test_unset_location_falls_back_to_the_configured_default(build, unset):
    # Specifically not a US region: a silent us-east4 is indistinguishable from
    # a deliberate choice in the run record, and wrong for an EU deployment.
    assert build(unset) == GOOGLE_VERTEX_DEFAULT_LOCATION
    assert build(unset) != "us-east4"


@pytest.mark.parametrize(
    "config_cls",
    [GoogleVertexLLMConfiguration, GoogleVertexRealtimeLLMConfiguration],
)
def test_registry_default_and_factory_fallback_are_the_same_value(config_cls):
    # The original defect was two defaults for one decision - "global" in the
    # registry, "us-east4" in the factory - so nothing an operator read told
    # them where their requests went. One constant is what keeps that fixed.
    schema = config_cls.model_json_schema()["properties"]["location"]

    assert schema["default"] == GOOGLE_VERTEX_DEFAULT_LOCATION
    assert _build_llm(None) == schema["default"]


@pytest.mark.parametrize(
    "config_cls",
    [GoogleVertexLLMConfiguration, GoogleVertexRealtimeLLMConfiguration],
)
def test_residency_bearing_locations_are_offered_in_the_schema(config_cls):
    # The form renders from this schema, so the multi-regions have to be listed
    # for an operator to pick one without knowing Vertex's location taxonomy.
    schema = config_cls.model_json_schema()["properties"]["location"]

    assert schema["examples"] == list(GOOGLE_VERTEX_LOCATIONS)
    assert {"eu", "us"}.issubset(set(schema["examples"]))
    assert schema["allow_custom_input"] is True


def test_fallback_is_reported_rather_than_silent():
    # The failure mode being fixed is invisibility, not the region itself: an
    # unchosen endpoint has to show up somewhere an operator can find it.
    with patch("api.services.pipecat.service_factory.logger") as logger:
        _build_llm(None)

    assert logger.warning.called
    message = logger.warning.call_args.args[0]
    assert GOOGLE_VERTEX_DEFAULT_LOCATION in message
    assert "residency" in message.lower()


def test_configured_location_is_recorded_too():
    with patch("api.services.pipecat.service_factory.logger") as logger:
        _build_llm("eu")

    assert any("eu" in call.args[0] for call in logger.info.call_args_list)
