from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import (
    InworldTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.service_factory import create_tts_service


def test_inworld_tts_configuration_defaults():
    config = InworldTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.INWORLD
    assert config.model == "inworld-tts-2"
    assert config.voice == "Ashley"
    assert config.language == "en-US"
    assert config.delivery_mode == "BALANCED"


def test_create_inworld_tts_service_uses_websocket_service_without_http_session():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.INWORLD.value,
            api_key="test-key",
            model="inworld-tts-2",
            voice="Ashley",
            speed=1.1,
            language="en-US",
            delivery_mode="CREATIVE",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with (
        patch("api.services.pipecat.service_factory.aiohttp.ClientSession") as session,
        patch("api.services.pipecat.service_factory.InworldTTSService") as mock_service,
    ):
        create_tts_service(user_config, audio_config)

    session.assert_not_called()
    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert "aiohttp_session" not in kwargs
    assert "streaming" not in kwargs
    assert kwargs["settings"].model == "inworld-tts-2"
    assert kwargs["settings"].voice == "Ashley"
    assert kwargs["settings"].language == "en-US"
    assert kwargs["settings"].speaking_rate == 1.1
    assert kwargs["settings"].delivery_mode == "CREATIVE"
