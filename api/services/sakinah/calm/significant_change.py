"""Configurable detection of material score movement."""

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class SignificantChangeConfig:
    single_turn_delta: float = 1.0
    persistent_turns: int = 3
    high_score: float = 8.0
    low_score: float = 2.0
    adverse_parameters: frozenset[str] = field(default_factory=frozenset)
    protective_parameters: frozenset[str] = field(default_factory=frozenset)


def detect_significant_change(
    trend: Mapping[str, Mapping[str, Any]],
    *,
    safety_state: Mapping[str, Any] | None = None,
    config: SignificantChangeConfig | None = None,
) -> dict[str, Any]:
    config = config or SignificantChangeConfig()
    reasons: list[str] = []
    for parameter, state in trend.items():
        name = parameter.replace("_", " ").capitalize()
        delta = state.get("delta_previous")
        if isinstance(delta, (int, float)) and abs(delta) >= config.single_turn_delta:
            reasons.append(f"{name} moved by {delta:+.1f} in one turn.")
        if state.get("persistence", 0) >= config.persistent_turns and state.get(
            "direction"
        ) in {"up", "down"}:
            reasons.append(
                f"{name} is moving {state['direction']} across consecutive turns."
            )
        current = state.get("current_score")
        if (
            isinstance(current, (int, float))
            and parameter in config.adverse_parameters
            and current >= config.high_score
            and state.get("persistence", 0) >= config.persistent_turns
        ):
            reasons.append(f"{name} is persistently high.")
        if (
            isinstance(current, (int, float))
            and parameter in config.protective_parameters
            and current <= config.low_score
            and state.get("persistence", 0) >= config.persistent_turns
        ):
            reasons.append(f"{name} is persistently low.")
    if safety_state and safety_state.get("requires_immediate_action"):
        reasons.append("The independent safety system requires immediate handling.")
    worsening_adverse = [
        name
        for name, state in trend.items()
        if name in config.adverse_parameters
        and state.get("direction") == "up"
        and state.get("persistence", 0) >= 2
    ]
    improving_protective = [
        name
        for name, state in trend.items()
        if name in config.protective_parameters and state.get("direction") == "up"
    ]
    if len(worsening_adverse) >= 2:
        reasons.append("Several configured adverse dimensions are worsening together.")
    if worsening_adverse and improving_protective:
        reasons.append(
            "Configured adverse dimensions are worsening while engagement or protection improves."
        )
    return {
        "significant_change": bool(reasons),
        "summary": " ".join(reasons),
        "reasons": reasons,
    }
