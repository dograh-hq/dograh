import pytest
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    EndFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMContextFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregator,
    LLMUserAggregatorParams,
)
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.turns.user_start import (
    ExternalUserTurnStartStrategy,
    MinWordsUserTurnStartStrategy,
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
from pipecat.turns.user_turn_strategies import UserTurnStrategies

import api.services.pipecat.run_pipeline as run_pipeline_module
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.run_pipeline import (
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


def test_nova_sonic_uses_local_vad_without_local_interruptions():
    strategies, vad_analyzer = _create_realtime_user_turn_config(
        ServiceProviders.AWS_NOVA_SONIC.value
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
    assert strategies.start[0]._enable_interruptions is True
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
    assert strategies.start[0]._enable_interruptions is True
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
    assert strategies.start[0]._enable_interruptions is True
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


def test_non_realtime_voice_activity_uses_external_start_for_external_turn_stt():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "default"},
        uses_external_turns=True,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], ExternalUserTurnStartStrategy)
    assert strategies[0]._enable_interruptions is True


def test_non_realtime_voice_activity_uses_transcription_and_vad_for_standard_stt():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "default"},
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


def test_external_turn_stt_honors_an_explicit_min_words_request():
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "min_words", "turn_start_min_words": 4},
        uses_external_turns=True,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], MinWordsUserTurnStartStrategy)
    assert strategies[0]._min_words == 4


@pytest.mark.parametrize("uses_external_turns", [False, True])
@pytest.mark.parametrize(
    "config",
    [
        {},
        {"turn_start_strategy": None, "turn_start_min_words": None},
        {"turn_start_strategy": "provisional_vad"},
    ],
    ids=["unset", "null", "retired"],
)
def test_non_realtime_defaults_to_two_word_interruptions(config, uses_external_turns):
    strategies = _create_non_realtime_user_turn_start_strategies(
        config,
        uses_external_turns=uses_external_turns,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], MinWordsUserTurnStartStrategy)
    assert strategies[0]._min_words == 2


@pytest.mark.parametrize("uses_external_turns", [False, True])
def test_non_realtime_min_words_start_strategy_has_default_threshold(
    uses_external_turns,
):
    strategies = _create_non_realtime_user_turn_start_strategies(
        {"turn_start_strategy": "min_words"},
        uses_external_turns=uses_external_turns,
    )

    assert len(strategies) == 1
    assert isinstance(strategies[0], MinWordsUserTurnStartStrategy)
    assert strategies[0]._min_words == DEFAULT_TURN_START_MIN_WORDS


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


@pytest.mark.parametrize(
    "bot_speaking,text,send_interim,accepted",
    [
        (True, "okay", True, False),
        (True, "please wait", True, True),
        (False, "yes", True, True),
        (True, "please wait", False, True),
    ],
    ids=["below-threshold", "interim-interruption", "silent-bot", "final-only"],
)
async def test_default_min_words_with_provider_turn_markers(
    bot_speaking, text, send_interim, accepted
):
    config = {}
    context = LLMContext()
    user = LLMUserAggregator(
        context,
        params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=_create_non_realtime_user_turn_start_strategies(
                    config, uses_external_turns=True
                ),
                stop=_create_non_realtime_user_turn_stop_strategies(
                    config, uses_external_turns=True
                ),
            ),
        ),
    )
    snapshots = {}

    @user.event_handler("on_before_process_frame")
    async def capture_turn_state(aggregator, frame):
        if isinstance(
            frame,
            (
                InterimTranscriptionFrame,
                TranscriptionFrame,
                ProposedUserStoppedSpeakingFrame,
                EndFrame,
            ),
        ):
            snapshots[type(frame)] = (
                aggregator.user_turn_controller.has_active_user_turn,
                [message["content"] for message in context.messages],
            )

    frames = [BotStartedSpeakingFrame(), SleepFrame()] if bot_speaking else []
    frames.extend([ProposedUserStartedSpeakingFrame(), VADUserStartedSpeakingFrame()])
    if send_interim:
        frames.extend([InterimTranscriptionFrame(text, "user", "now"), SleepFrame()])
    frames.extend(
        [
            VADUserStoppedSpeakingFrame(),
            TranscriptionFrame(text, "user", "now", finalized=True),
            ProposedUserStoppedSpeakingFrame(),
            SleepFrame(),
        ]
    )
    down, _ = await run_test(user, frames_to_send=frames)

    # Neither the provider's onset nor local VAD bypasses the word threshold.
    if send_interim:
        assert snapshots[InterimTranscriptionFrame] == (False, [])
    assert snapshots[TranscriptionFrame] == (accepted and send_interim, [])
    # The complete transcript waits for the provider's stop before running the LLM.
    assert snapshots[ProposedUserStoppedSpeakingFrame] == (accepted, [])
    assert snapshots[EndFrame] == (False, [text] if accepted else [])
    for frame_type in (
        UserStartedSpeakingFrame,
        InterruptionFrame,
        LLMContextFrame,
        UserStoppedSpeakingFrame,
    ):
        assert sum(isinstance(frame, frame_type) for frame in down) == int(accepted)
