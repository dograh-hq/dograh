from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.turns.user_start import (
    ExternalUserTurnStartStrategy,
)
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop import (
    ExternalUserTurnStopStrategy,
    SpeechTimeoutUserTurnStopStrategy,
)

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.run_pipeline import _create_realtime_user_turn_config


def test_gemini_realtime_uses_local_vad_without_local_interruptions():
    strategies, vad_analyzer = _create_realtime_user_turn_config(
        ServiceProviders.GOOGLE_REALTIME.value
    )

    assert isinstance(vad_analyzer, SileroVADAnalyzer)
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], VADUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is False
    assert len(strategies.stop) == 1
    assert isinstance(strategies.stop[0], SpeechTimeoutUserTurnStopStrategy)


def test_gemini_vertex_realtime_uses_same_turn_config_as_gemini_live():
    strategies, vad_analyzer = _create_realtime_user_turn_config(
        ServiceProviders.GOOGLE_VERTEX_REALTIME.value
    )

    assert isinstance(vad_analyzer, SileroVADAnalyzer)
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], VADUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is False


def test_openai_realtime_uses_provider_turn_frames_without_local_vad():
    strategies, vad_analyzer = _create_realtime_user_turn_config(
        ServiceProviders.OPENAI_REALTIME.value
    )

    assert vad_analyzer is None
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], ExternalUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is False
    assert len(strategies.stop) == 1
    assert isinstance(strategies.stop[0], ExternalUserTurnStopStrategy)


def test_unknown_realtime_providers_keep_local_vad():
    strategies, vad_analyzer = _create_realtime_user_turn_config("other_realtime")

    assert isinstance(vad_analyzer, SileroVADAnalyzer)
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], VADUserTurnStartStrategy)
    assert len(strategies.stop) == 1
    assert isinstance(strategies.stop[0], SpeechTimeoutUserTurnStopStrategy)
