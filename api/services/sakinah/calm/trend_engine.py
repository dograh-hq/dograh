"""Generic score trajectory calculations.

The engine deliberately knows nothing about clinical parameter names.  A score
is any numeric value (or a mapping containing ``score`` and ``confidence``), so
the same component can be reused by another CALM configuration or live STT.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class TrendConfig:
    unchanged_epsilon: float = 0.0001
    rapid_velocity: float = 0.75
    persistent_turns: int = 3


def _number(value: Any) -> float | None:
    if isinstance(value, Mapping):
        value = value.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if isfinite(value) else None


def _direction(delta: float | None, epsilon: float) -> str:
    if delta is None:
        return "insufficient_data"
    if abs(delta) <= epsilon:
        return "unchanged"
    return "up" if delta > 0 else "down"


def _persistence(values: list[float], epsilon: float) -> int:
    if len(values) < 2:
        return 0
    latest = values[-1]
    sign = (
        1
        if latest - values[-2] > epsilon
        else -1
        if latest - values[-2] < -epsilon
        else 0
    )
    if sign == 0:
        count = 1
        for current, previous in zip(reversed(values[:-1]), reversed(values[:-2])):
            if abs(current - previous) <= epsilon:
                count += 1
            else:
                break
        return count
    count = 1
    for current, previous in zip(reversed(values[:-1]), reversed(values[:-2])):
        change = current - previous
        if (change > epsilon and sign == 1) or (change < -epsilon and sign == -1):
            count += 1
        else:
            break
    return count


class TrendEngine:
    """Calculate previous-turn and multi-turn movement for arbitrary scores."""

    def __init__(self, config: TrendConfig | None = None):
        self.config = config or TrendConfig()

    def calculate(
        self,
        current_scores: Mapping[str, Any],
        previous_scores: Mapping[str, Any] | None = None,
        history: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        previous_scores = previous_scores or {}
        history = history or []
        result: dict[str, dict[str, Any]] = {}

        for parameter, raw_current in current_scores.items():
            current = _number(raw_current)
            previous = _number(previous_scores.get(parameter))
            previous_delta = (
                current - previous
                if current is not None and previous is not None
                else None
            )
            values = [
                value
                for value in (_number(scores.get(parameter)) for scores in history)
                if value is not None
            ]
            if current is not None:
                values.append(current)

            # A three-turn window is the current value plus the two immediately
            # preceding values.  This also matches the product's displayed
            # four-turn fixture (2.8 - 5.2 = -2.4).
            delta_3_turns = (
                current - values[-3]
                if current is not None and len(values) >= 3
                else None
            )
            baseline = current - values[0] if current is not None and values else None
            direction = _direction(previous_delta, self.config.unchanged_epsilon)
            persistence = _persistence(values, self.config.unchanged_epsilon)
            velocity = "insufficient_data"
            if previous_delta is not None:
                velocity = (
                    "rapid"
                    if abs(previous_delta) >= self.config.rapid_velocity
                    else "gradual"
                    if abs(previous_delta) > self.config.unchanged_epsilon
                    else "stable"
                )
            confidence = (
                raw_current.get("confidence")
                if isinstance(raw_current, Mapping)
                else None
            )
            result[parameter] = {
                "current_score": current,
                "previous_score": previous,
                "delta_previous": round(previous_delta, 6)
                if previous_delta is not None
                else None,
                "delta_3_turns": round(delta_3_turns, 6)
                if delta_3_turns is not None
                else None,
                "conversation_baseline_delta": round(baseline, 6)
                if baseline is not None
                else None,
                "baseline_delta": round(baseline, 6) if baseline is not None else None,
                "direction": direction,
                "persistence": persistence,
                "velocity": velocity,
                "confidence": confidence,
            }
        return result

    def interpret(self, trend: Mapping[str, Mapping[str, Any]]) -> list[str]:
        """Return compact statements suitable for an engineered LLM prompt."""
        statements: list[str] = []
        for parameter, state in trend.items():
            name = parameter.replace("_", " ").capitalize()
            direction = state.get("direction")
            persistence = int(state.get("persistence") or 0)
            score = state.get("current_score")
            if direction == "insufficient_data":
                continue
            if direction == "unchanged":
                if score is not None and float(score) >= 7:
                    statements.append(f"{name} remains persistently high and stable.")
                else:
                    statements.append(f"{name} remains largely unchanged.")
            elif direction == "down":
                qualifier = (
                    "across consecutive turns"
                    if persistence >= self.config.persistent_turns
                    else ""
                )
                statements.append(
                    f"{name} is declining {qualifier}.".replace("  ", " ")
                )
            else:
                qualifier = (
                    "across consecutive turns"
                    if persistence >= self.config.persistent_turns
                    else ""
                )
                statements.append(
                    f"{name} is increasing {qualifier}.".replace("  ", " ")
                )
        return statements
