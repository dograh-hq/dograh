"""Turn-level CALM orchestration for the AI-to-AI simulation.

The runtime is intentionally independent of Pipecat.  Its only inputs are the
same inputs a future live-speech adapter will have after final STT.
"""

from typing import Any, Mapping

from .clinical import evaluate_clinical_context
from .prompt_composer import PromptComposer
from .response_strategy import ResponseStrategyEngine
from .safety import score_safety
from .scorer import score_utterance
from .significant_change import SignificantChangeConfig, detect_significant_change
from .trend_engine import TrendConfig, TrendEngine

EXPERIMENT_MODES = (
    "baseline",
    "scores_only",
    "scores_and_trends",
    "full_calm_prompt",
)


class CalmSimulationRuntime:
    def __init__(
        self,
        *,
        mode: str = "full_calm_prompt",
        scenario: str = "",
        trend_config: TrendConfig | None = None,
        significant_change_config: SignificantChangeConfig | None = None,
    ):
        if mode not in EXPERIMENT_MODES:
            raise ValueError(f"Unsupported CALM experiment mode: {mode}")
        self.mode = mode
        self.scenario = scenario
        self.trend_engine = TrendEngine(trend_config)
        self.significant_change_config = significant_change_config
        self.strategy_engine = ResponseStrategyEngine()
        self.prompt_composer = PromptComposer()
        self.score_history: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self._base_role_rules = (
            "You are Sakinah, a compassionate voice-based clinical conversation "
            "partner and listener. Stay as Sakinah, listen carefully, and respond "
            "concisely and naturally for speech."
            + (f" The supported person's scenario is: {scenario}" if scenario else "")
        )

    def analyze_turn(
        self,
        utterance_verbatim: str,
        *,
        conversation_context: list[Mapping[str, Any]] | None = None,
        parameters: tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(utterance_verbatim, str) or not utterance_verbatim:
            raise ValueError("utterance_verbatim must be a non-empty string")
        calm_scores = score_utterance(
            utterance_verbatim, parameters or ("hope", "anxiety", "trust", "loneliness")
        )
        previous_scores = self.score_history[-1] if self.score_history else {}
        trend_parameters = self.trend_engine.calculate(
            calm_scores, previous_scores, self.score_history
        )
        interpretations = self.trend_engine.interpret(trend_parameters)
        safety_state = score_safety(utterance_verbatim)
        clinical_evaluation = evaluate_clinical_context(utterance_verbatim, calm_scores)
        significant = detect_significant_change(
            trend_parameters,
            safety_state=safety_state,
            config=self.significant_change_config,
        )
        strategy = self.strategy_engine.generate(
            utterance_verbatim=utterance_verbatim,
            calm_scores=calm_scores,
            trend=trend_parameters,
            significant_change=significant,
            safety_state=safety_state,
            clinical_evaluation=clinical_evaluation,
            recent_history=self.turns,
        )
        trend_payload = {
            "parameters": trend_parameters,
            "interpretations": interpretations,
            "significant_change": significant.get("summary", ""),
        }
        prompt = self.prompt_composer.compose(
            utterance_verbatim=utterance_verbatim,
            conversation_context=conversation_context,
            calm_state=calm_scores,
            trend_state=trend_payload,
            safety_state=safety_state,
            clinical_evaluation=clinical_evaluation,
            response_strategy=strategy,
            mode=self.mode,
            role_rules=self._base_role_rules,
        )
        if self.mode == "baseline":
            prompt = self._base_role_rules
        turn = {
            "turn_id": len(self.turns) + 1,
            "utterance_verbatim": utterance_verbatim,
            "calm_scores": {
                name: value.get("score") for name, value in calm_scores.items()
            },
            "calm_confidence": {
                name: value.get("confidence") for name, value in calm_scores.items()
            },
            "evidence_spans": {
                name: value.get("evidence", []) for name, value in calm_scores.items()
            },
            "trend": trend_payload,
            "significant_changes": significant,
            "safety_state": safety_state,
            "clinical_evaluation": clinical_evaluation,
            "response_strategy": strategy,
            "prompt_sent_to_llm": prompt,
            "llm_response_raw": "",
            "response_delivered": "",
            "post_response_evaluation": {},
        }
        self.score_history.append(calm_scores)
        self.turns.append(turn)
        return turn

    def record_response(self, response: str) -> None:
        if not self.turns:
            return
        self.turns[-1]["llm_response_raw"] = response
        self.turns[-1]["response_delivered"] = response
        self.turns[-1]["post_response_evaluation"] = {
            "response_present": bool(response.strip()),
            "safety_decision_changed": False,
        }
