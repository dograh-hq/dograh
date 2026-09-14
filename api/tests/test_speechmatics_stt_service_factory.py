from types import SimpleNamespace

import pytest
from pipecat.services.speechmatics.stt import SpeechmaticsSTTService, TurnDetectionMode

from api.services.configuration.registry import SpeechmaticsSTTConfiguration
from api.services.pipecat.service_factory import create_stt_service


@pytest.mark.parametrize("model", ["linden-1", "enhanced", "standard"])
def test_speechmatics_factory_uses_agent_stt_and_preserves_local_turn_detection(model):
    config = SpeechmaticsSTTConfiguration(
        api_key="test-key", model=model, language="en"
    )
    service = create_stt_service(
        SimpleNamespace(stt=config),
        SimpleNamespace(transport_in_sample_rate=16000),
        keyterms=["Dograh"],
    )

    assert isinstance(service, SpeechmaticsSTTService)
    assert service._settings.model == "linden-1"
    assert service._settings.turn_detection_mode == TurnDetectionMode.EXTERNAL
    assert service._settings.additional_vocab[0].content == "Dograh"
    assert service.service_metadata_frame().user_turn_strategies is None


def test_speechmatics_default_is_current_agent_model():
    assert SpeechmaticsSTTConfiguration(api_key="test-key").model == "linden-1"
