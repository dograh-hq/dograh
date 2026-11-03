from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import TypeAdapter

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    REGISTRY,
    ServiceProviders,
    ServiceType,
    TTSConfig,
)
from api.services.pipecat.service_factory import create_tts_service


def test_stored_lmnt_configuration_remains_readable_but_is_not_offered():
    config = TypeAdapter(TTSConfig).validate_python(
        {"provider": "lmnt", "api_key": "test-key", "model": "aurora", "voice": "lily"}
    )
    assert config.provider == ServiceProviders.LMNT
    assert config.voice == "lily"
    assert ServiceProviders.LMNT not in REGISTRY[ServiceType.TTS]


def test_lmnt_call_reports_retired_provider():
    config = SimpleNamespace(tts=SimpleNamespace(provider="lmnt", model="aurora"))
    with pytest.raises(ValueError, match="LMNT is no longer available"):
        create_tts_service(config, SimpleNamespace())


def test_lmnt_key_validation_rejects_retired_provider_without_network():
    with patch("api.services.configuration.check_validity.httpx.get") as request:
        with pytest.raises(ValueError, match="LMNT is no longer available"):
            UserConfigurationValidator()._check_lmnt_api_key("aurora", "test-key")
    request.assert_not_called()
