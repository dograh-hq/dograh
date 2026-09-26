"""Response strategy generation, subordinate to the safety system."""

from typing import Any, Mapping


class ResponseStrategyEngine:
    def generate(
        self,
        *,
        utterance_verbatim: str,
        calm_scores: Mapping[str, Any],
        trend: Mapping[str, Mapping[str, Any]],
        significant_change: Mapping[str, Any],
        safety_state: Mapping[str, Any],
        clinical_evaluation: Mapping[str, Any],
        recent_history: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if safety_state.get("requires_immediate_action"):
            return {
                "primary_objective": "Follow the independent immediate-safety protocol.",
                "secondary_objective": "Keep the service user engaged while safety support is arranged.",
                "tone": "warm, calm and serious",
                "behaviours": [
                    "Acknowledge the actual statement",
                    "Ask one direct safety-focused question",
                ],
                "avoid": [
                    "reassurance that minimizes risk",
                    "delaying safety handling",
                ],
            }
        worsening = [
            parameter.replace("_", " ")
            for parameter, state in trend.items()
            if state.get("direction") == "down" and state.get("persistence", 0) >= 2
        ]
        focus = (
            worsening[0]
            if worsening
            else "what the service user means by their statement"
        )
        focus_phrase = f"worsening {focus}" if worsening else focus
        return {
            "primary_objective": f"Explore the service user's {focus_phrase}.",
            "secondary_objective": "Maintain engagement and therapeutic connection.",
            "tone": "warm, calm and serious",
            "behaviours": [
                "Acknowledge the service user's actual statement",
                "Reflect the meaning without adding unsupported conclusions",
                "Ask one focused exploratory question",
            ],
            "avoid": [
                "generic reassurance",
                "forced optimism",
                "multiple simultaneous questions",
            ],
        }
