from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import BotStartedSpeakingFrame, TranscriptionFrame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import (
    ExternalUserTurnStartStrategy,
    MinWordsUserTurnStartStrategy,
    ProvisionalVADUserTurnStartStrategy,
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop import (
    ExternalUserTurnStopStrategy,
    SpeechTimeoutUserTurnStopStrategy,
    TurnAnalyzerUserTurnStopStrategy,
)

import api.services.pipecat.run_pipeline as run_pipeline_module
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.run_pipeline import (
    DEFAULT_PROVISIONAL_VAD_PAUSE_SECS,
    DEFAULT_TURN_START_MIN_WORDS,
    DEFAULT_USER_TURN_STOP_TIMEOUT,
    EXTERNAL_TURN_USER_STOP_TIMEOUT,
    _create_non_realtime_user_turn_start_strategies,
    _create_non_realtime_user_turn_stop_strategies,
    _create_realtime_user_turn_config,
    _resolve_user_turn_stop_timeout,
)


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
    assert strategies.stop[0].wait_for_transcript is False


def test_gemini_vertex_realtime_uses_same_turn_config_as_gemini_live():
    strategies, vad_analyzer = _create_realtime_user_turn_config(
        ServiceProviders.GOOGLE_VERTEX_REALTIME.value
    )

    assert isinstance(vad_analyzer, SileroVADAnalyzer)
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], VADUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is False
    assert len(strategies.stop) == 1
    assert isinstance(strategies.stop[0], SpeechTimeoutUserTurnStopStrategy)
    assert strategies.stop[0].wait_for_transcript is False


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
    assert strategies.stop[0].wait_for_transcript is False


def test_azure_realtime_uses_provider_turn_frames_without_local_vad():
    strategies, vad_analyzer = _create_realtime_user_turn_config(
        ServiceProviders.AZURE_REALTIME.value
    )

    assert vad_analyzer is None
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], ExternalUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is False
    assert len(strategies.stop) == 1
    assert isinstance(strategies.stop[0], ExternalUserTurnStopStrategy)
    assert strategies.stop[0].wait_for_transcript is False


def test_grok_realtime_uses_provider_turn_frames_without_local_vad():
    strategies, vad_analyzer = _create_realtime_user_turn_config(
        ServiceProviders.GROK_REALTIME.value
    )

    assert vad_analyzer is None
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], ExternalUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is False
    assert len(strategies.stop) == 1
    assert isinstance(strategies.stop[0], ExternalUserTurnStopStrategy)
    assert strategies.stop[0].wait_for_transcript is False


def test_ultravox_realtime_uses_local_vad_with_local_interruptions():
    strategies, vad_analyzer = _create_realtime_user_turn_config(
        ServiceProviders.ULTRAVOX_REALTIME.value
    )

    assert isinstance(vad_analyzer, SileroVADAnalyzer)
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], VADUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is True
    assert len(strategies.stop) == 1
    assert isinstance(strategies.stop[0], SpeechTimeoutUserTurnStopStrategy)
    assert strategies.stop[0].wait_for_transcript is False


def test_unknown_realtime_providers_keep_local_vad():
    strategies, vad_analyzer = _create_realtime_user_turn_config("other_realtime")

    assert isinstance(vad_analyzer, SileroVADAnalyzer)
    assert len(strategies.start) == 1
    assert isinstance(strategies.start[0], VADUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is True
    assert len(strategies.stop) == 1
    assert isinstance(strategies.stop[0], SpeechTimeoutUserTurnStopStrategy)
    assert strategies.stop[0].wait_for_transcript is False


def test_non_realtime_default_uses_external_start_for_external_turn_stt():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {},
        uses_external_turns=True,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], ExternalUserTurnStartStrategy)
    assert strategies[0]._enable_interruptions is True


def test_non_realtime_default_uses_transcription_fallback_and_vad_for_standard_stt():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {},
        uses_external_turns=False,
    )

    assert len(strategies) == 2
    assert isinstance(strategies[0], TranscriptionUserTurnStartStrategy)
    assert isinstance(strategies[1], VADUserTurnStartStrategy)


def test_non_realtime_can_use_min_words_start_strategy():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "min_words", "turn_start_min_words": 4},
        uses_external_turns=False,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], MinWordsUserTurnStartStrategy)
    assert strategies[0]._min_words == 4


def test_non_realtime_explicit_min_words_overrides_external_turn_default():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "min_words", "turn_start_min_words": 4},
        uses_external_turns=True,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], MinWordsUserTurnStartStrategy)
    assert strategies[0]._min_words == 4


def test_non_realtime_min_words_start_strategy_has_default_threshold():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "min_words"},
        uses_external_turns=False,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], MinWordsUserTurnStartStrategy)
    assert strategies[0]._min_words == DEFAULT_TURN_START_MIN_WORDS


def test_non_realtime_can_use_provisional_vad_start_strategy():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "provisional_vad"},
        uses_external_turns=False,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], ProvisionalVADUserTurnStartStrategy)
    assert strategies[0]._pause_secs == DEFAULT_PROVISIONAL_VAD_PAUSE_SECS


def test_non_realtime_provisional_vad_uses_configured_pause_secs():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "provisional_vad", "provisional_vad_pause_secs": 0.4},
        uses_external_turns=False,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], ProvisionalVADUserTurnStartStrategy)
    assert strategies[0]._pause_secs == 0.4


async def test_non_realtime_provisional_vad_starts_on_transcript_without_vad():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "provisional_vad"},
        uses_external_turns=False,
    )
    strategy = strategies[0]
    turn_started = False

    @strategy.event_handler("on_user_turn_started")
    async def on_user_turn_started(strategy, params):
        nonlocal turn_started
        turn_started = True

    await strategy.process_frame(BotStartedSpeakingFrame())
    result = await strategy.process_frame(
        TranscriptionFrame(text="Hello", user_id="user", timestamp="")
    )

    assert result == ProcessFrameResult.STOP
    assert turn_started is True


def test_non_realtime_uses_external_stop_for_external_turn_stt():
    strategies = _create_non_realtime_user_turn_stop_strategies(
        {},
        uses_external_turns=True,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], ExternalUserTurnStopStrategy)


def test_non_realtime_default_uses_speech_timeout_stop():
    strategies = _create_non_realtime_user_turn_stop_strategies(
        {},
        uses_external_turns=False,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], SpeechTimeoutUserTurnStopStrategy)


def test_non_realtime_can_use_turn_analyzer_stop_strategy(monkeypatch):
    monkeypatch.setattr(
        run_pipeline_module,
        "LocalSmartTurnAnalyzerV3",
        lambda *, params: params,
    )

    strategies = _create_non_realtime_user_turn_stop_strategies(
        {"turn_stop_strategy": "turn_analyzer", "smart_turn_stop_secs": 1.5},
        uses_external_turns=False,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], TurnAnalyzerUserTurnStopStrategy)
    assert strategies[0]._turn_analyzer.stop_secs == 1.5


def test_external_turn_stt_uses_longer_stop_timeout():
    assert (
        _resolve_user_turn_stop_timeout({}, uses_external_turns=True)
        == EXTERNAL_TURN_USER_STOP_TIMEOUT
    )


def test_standard_stt_keeps_default_stop_timeout():
    assert (
        _resolve_user_turn_stop_timeout({}, uses_external_turns=False)
        == DEFAULT_USER_TURN_STOP_TIMEOUT
    )


def test_workflow_config_can_override_user_turn_stop_timeout():
    assert (
        _resolve_user_turn_stop_timeout(
            {"user_turn_stop_timeout": "12.5"},
            uses_external_turns=True,
        )
        == 12.5
    )
