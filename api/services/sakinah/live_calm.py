"""Shared live-call CALM scoring for tester, WebRTC and telephony runs."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.sakinah.calm.runtime import CalmSimulationRuntime
from api.services.sakinah.calm.safety import score_safety
from api.services.sakinah.calm.scorer import score_utterance
from api.services.workflow_run_artifacts import persist_calm_scoring_artifact


class LiveCalmSession:
    """Own one run's turn history and publish/persist it incrementally."""

    def __init__(self, workflow_run_id: int, *, scenario: str = "") -> None:
        self.workflow_run_id = workflow_run_id
        self.runtime = CalmSimulationRuntime(mode="full_calm_prompt", scenario=scenario)
        self.last_user_utterance: str | None = None
        self.sakinah_turns: list[dict[str, Any]] = []
        self._persist_lock = asyncio.Lock()

    def analyse_user(self, utterance: str, context: list[dict[str, Any]]) -> dict[str, Any] | None:
        text = utterance.strip()
        if not text or text == self.last_user_utterance:
            return None
        self.last_user_utterance = text
        turn = self.runtime.analyze_turn(text, conversation_context=context)
        # Keep graph/table fields stable across simulation and live calls.
        safety = turn.get("safety_state") or {}
        turn["emotional_scores"] = dict(turn.get("calm_scores") or {})
        turn["safety_scores"] = {
            "risk": 10.0
            if safety.get("requires_immediate_action")
            else 7.0
            if safety.get("classification") == "concern"
            else 0.0
        }
        turn["role"] = "caller"
        return turn

    def record_sakinah(self, response: str) -> dict[str, Any] | None:
        text = (response or "").strip()
        if not text:
            return None
        scores = score_utterance(
            text, ("empathy", "emotional_attunement", "validation")
        )
        previous = self.sakinah_turns[-1].get("scores", {}) if self.sakinah_turns else {}
        values = {name: value.get("score") for name, value in scores.items()}
        trend = {
            name: {
                "previous_score": previous.get(name),
                "delta_previous": None if previous.get(name) is None else round(value - previous[name], 3),
                "direction": "insufficient_data" if previous.get(name) is None else ("up" if value > previous[name] else "down" if value < previous[name] else "unchanged"),
            }
            for name, value in values.items()
        }
        turn = {
            "turn_id": len(self.sakinah_turns) + 1,
            "role": "sakinah",
            "text": text,
            "scores": values,
            "emotional_scores": values,
            "safety_scores": {
                "risk": 10.0
                if score_safety(text).get("requires_immediate_action")
                else 0.0
            },
            "confidence": {name: value.get("confidence") for name, value in scores.items()},
            "trend": trend,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.sakinah_turns.append(turn)
        return turn

    def payload(self) -> dict[str, Any]:
        return {
            "version": 2,
            "workflow_run_id": self.workflow_run_id,
            "updated_at": datetime.now(UTC).isoformat(),
            "caller": self.runtime.turns,
            "sakinah": self.sakinah_turns,
        }

    async def persist(self) -> None:
        payload = self.payload()
        async with self._persist_lock:
            try:
                await db_client.update_workflow_run(
                    run_id=self.workflow_run_id,
                    annotations={"calm_scoring": payload},
                )
            except Exception as exc:  # scoring must never interrupt a call
                logger.warning(
                    "CALM database persistence failed for run {}: {}",
                    self.workflow_run_id,
                    type(exc).__name__,
                )
            try:
                await persist_calm_scoring_artifact(self.workflow_run_id, payload)
            except Exception as exc:  # object persistence is best effort during live audio
                logger.warning(
                    "CALM object persistence failed for run {}: {}",
                    self.workflow_run_id,
                    type(exc).__name__,
                )
