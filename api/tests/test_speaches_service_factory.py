from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.service_factory import create_stt_service


def test_create_speaches_stt_service_uses_http_base_url():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.SPEACHES.value,
            base_url="http://localhost:9100/v1",
            api_key=None,
            model="Systran/faster-whisper-small",
            language="tr",
        )
    )
    audio_config = SimpleNamespace(transport_in_sample_rate=16000)

    with patch(
        "api.services.pipecat.service_factory.SpeachesSTTService"
    ) as mock_service:
        create_stt_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["base_url"] == "http://localhost:9100/v1"
    assert kwargs["api_key"] == "none"
    assert kwargs["settings"].model == "Systran/faster-whisper-small"
    assert kwargs["settings"].language == "tr"
