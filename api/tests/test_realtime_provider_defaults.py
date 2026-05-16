from api.services.configuration.defaults import DEFAULT_SERVICE_PROVIDERS
from api.services.configuration.registry import REGISTRY, ServiceType


def test_openai_realtime_is_exposed_in_default_configurations():
    assert "openai_realtime" in REGISTRY[ServiceType.REALTIME]
    assert DEFAULT_SERVICE_PROVIDERS["realtime"] == "google_realtime"
