"""The Vertex realtime default and its model list must not name a retired model.

Google retires ``gemini-live-2.5-flash-native-audio`` on Vertex AI on
2026-12-13; requests that name it fail afterwards. A default that points at it
breaks every Vertex realtime configuration created without an explicit model.
"""

from api.services.configuration.options import GOOGLE_VERTEX_REALTIME_MODELS
from api.services.configuration.registry import GoogleVertexRealtimeLLMConfiguration

RETIRED_MODEL = "google/gemini-live-2.5-flash-native-audio"


def test_vertex_realtime_default_is_not_retired():
    default = GoogleVertexRealtimeLLMConfiguration.model_fields["model"].default
    assert default != RETIRED_MODEL


def test_vertex_realtime_models_exclude_retired_and_include_default():
    default = GoogleVertexRealtimeLLMConfiguration.model_fields["model"].default
    assert RETIRED_MODEL not in GOOGLE_VERTEX_REALTIME_MODELS
    assert default in GOOGLE_VERTEX_REALTIME_MODELS
