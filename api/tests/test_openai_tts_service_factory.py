from types import SimpleNamespace
from unittest.mock import patch

from pipecat.services.openai._constants import OPENAI_SAMPLE_RATE

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.service_factory import create_tts_service


def test_create_openai_tts_service_uses_openai_pcm_sample_rate():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.OPENAI.value,
            api_key="test-key",
            model="gpt-4o-mini-tts",
            voice="alloy",
            base_url=None,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=16000,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.OpenAITTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["sample_rate"] == OPENAI_SAMPLE_RATE
    assert kwargs["settings"].model == "gpt-4o-mini-tts"
