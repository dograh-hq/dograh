"""Unit coverage for the reusable Stage 4 CALM components."""

from api.services.sakinah.calm.prompt_composer import PromptComposer
from api.services.sakinah.calm.response_strategy import ResponseStrategyEngine
from api.services.sakinah.calm.runtime import EXPERIMENT_MODES, CalmSimulationRuntime
from api.services.sakinah.calm.safety import score_safety
from api.services.sakinah.calm.significant_change import detect_significant_change
from api.services.sakinah.calm.trend_engine import TrendEngine


def _scores(hope, anxiety, trust, loneliness):
    return {
        "hope": {"score": hope, "confidence": 0.91},
        "anxiety": {"score": anxiety, "confidence": 0.88},
        "trust": {"score": trust, "confidence": 0.87},
        "loneliness": {"score": loneliness, "confidence": 0.94},
    }


def test_required_multi_turn_fixture_uses_previous_score_and_numeric_movement():
    history = [
        _scores(6.0, 4.1, 3.2, 8.0),
        _scores(5.2, 5.0, 4.0, 8.1),
        _scores(4.0, 6.4, 5.3, 8.1),
    ]
    trend = TrendEngine().calculate(_scores(2.8, 7.2, 5.9, 8.1), history[-1], history)

    assert trend["hope"]["previous_score"] == 4.0
    assert trend["hope"]["delta_previous"] == -1.2
    assert trend["anxiety"]["delta_previous"] == 0.8
    assert trend["trust"]["delta_previous"] == 0.6
    assert trend["loneliness"]["delta_previous"] == 0
    assert trend["hope"]["delta_3_turns"] == -2.4
    assert trend["hope"]["direction"] == "down"
    assert trend["anxiety"]["direction"] == "up"
    assert trend["loneliness"]["direction"] == "unchanged"
    assert trend["hope"]["persistence"] >= 3


def test_first_turn_has_no_previous_score_and_is_not_unchanged():
    trend = TrendEngine().calculate(_scores(5, 5, 5, 5))
    assert trend["hope"]["previous_score"] is None
    assert trend["hope"]["delta_previous"] is None
    assert trend["hope"]["direction"] == "insufficient_data"


def test_significant_change_is_configurable_and_safety_is_independent():
    trend = TrendEngine().calculate(
        _scores(3, 8, 5, 8), _scores(5, 6, 5, 8), [_scores(5, 6, 5, 8)]
    )
    result = detect_significant_change(trend)
    assert result["significant_change"] is True
    assert score_safety("I want to die")["requires_immediate_action"] is True
    strategy = ResponseStrategyEngine().generate(
        utterance_verbatim="I want to die",
        calm_scores={},
        trend={},
        significant_change=result,
        safety_state=score_safety("I want to die"),
        clinical_evaluation={},
    )
    assert "safety" in strategy["primary_objective"].lower()
    assert "downgrade" not in strategy["primary_objective"].lower()


def test_runtime_preserves_verbatim_evidence_and_composes_full_prompt():
    utterance = "I don't see much point in talking anymore. Nothing seems to change."
    runtime = CalmSimulationRuntime()
    turn = runtime.analyze_turn(utterance)
    assert turn["utterance_verbatim"] == utterance
    assert "I don't see much point" in turn["evidence_spans"]["hope"]
    assert utterance in turn["prompt_sent_to_llm"]
    assert "CALM" in turn["prompt_sent_to_llm"]
    assert "scores" in turn["prompt_sent_to_llm"]


def test_prompt_composer_keeps_internal_analysis_out_of_service_user_output():
    prompt = PromptComposer().compose(
        utterance_verbatim="I feel alone.",
        calm_state={
            "loneliness": {"score": 8.1, "confidence": 0.94, "evidence": ["alone"]}
        },
        mode="full_calm_prompt",
    )
    assert '"I feel alone."' in prompt
    assert "Do not mention CALM" in prompt


def test_all_experiment_modes_are_supported():
    assert EXPERIMENT_MODES == (
        "baseline",
        "scores_only",
        "scores_and_trends",
        "full_calm_prompt",
    )
    for mode in EXPERIMENT_MODES:
        turn = CalmSimulationRuntime(mode=mode).analyze_turn("I feel okay today.")
        assert turn["prompt_sent_to_llm"]
