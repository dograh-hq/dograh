"""Shared live-call CALM scoring for tester, WebRTC and telephony runs."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.sakinah.calm.runtime import CalmSimulationRuntime
from api.services.sakinah.calm.safety import score_safety
from api.services.sakinah.calm.scorer import score_utterance
from api.services.sakinah.calm_evaluation import CalmEvaluator, enrich_scoring_turn
from api.services.workflow_run_artifacts import persist_calm_scoring_artifact


def latest_user_turn(messages: list[dict[str, Any]]) -> tuple[int, str] | None:
    """Read final text from normal and multimodal Pipecat user messages."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return index, content.strip()
        if isinstance(content, list):
            text = " ".join(
                str(part.get("text") or part.get("transcript") or "").strip()
                for part in content
                if isinstance(part, dict)
            ).strip()
            if text:
                return index, text
    return None


class LiveCalmSession:
    """Own one run's turn history and publish/persist it incrementally."""

    def __init__(
        self,
        workflow_run_id: int,
        *,
        scenario: str = "",
        evaluation_inference: Callable[[list[dict], str], Awaitable[str | None]] | None = None,
        on_update: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.workflow_run_id = workflow_run_id
        self.runtime = CalmSimulationRuntime(mode="full_calm_prompt", scenario=scenario)
        self.last_user_utterance: str | None = None
        self.last_user_turn_key: object | None = None
        self.sakinah_turns: list[dict[str, Any]] = []
        self._persist_lock = asyncio.Lock()
        self._evaluation_locks = {"caller": asyncio.Lock(), "sakinah": asyncio.Lock()}
        self._evaluation_tasks: set[asyncio.Task] = set()
        self._conversation: list[dict[str, str]] = []
        self._evaluator = CalmEvaluator(evaluation_inference) if evaluation_inference else None
        self._on_update = on_update

    def analyse_user(
        self,
        utterance: str,
        context: list[dict[str, Any]],
        *,
        source_turn_key: object | None = None,
    ) -> dict[str, Any] | None:
        text = utterance.strip()
        if not text or (
            text == self.last_user_utterance
            and (source_turn_key is None or source_turn_key == self.last_user_turn_key)
        ):
            return None
        self.last_user_utterance = text
        self.last_user_turn_key = source_turn_key
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
        self._conversation.append({"role": "service_user", "text": text})
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
            "prompt_sent_to_llm": (
                self.runtime.turns[-1].get("prompt_sent_to_llm", "")
                if self.runtime.turns else ""
            ),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.sakinah_turns.append(turn)
        self._conversation.append({"role": "sakinah", "text": text})
        return turn

    def schedule_evaluation(self, turn: dict[str, Any]) -> None:
        if self._evaluator is None:
            return
        role = str(turn["role"])
        transcript = list(self._conversation)
        task = asyncio.create_task(self._evaluate(turn, role, transcript))
        self._evaluation_tasks.add(task)
        task.add_done_callback(self._evaluation_tasks.discard)

    async def _evaluate(
        self, turn: dict[str, Any], role: str, transcript: list[dict[str, str]]
    ) -> None:
        try:
            async with self._evaluation_locks[role]:
                result = await self._evaluator.evaluate(
                    role="service_user" if role == "caller" else "sakinah",
                    turn_id=str(turn["turn_id"]),
                    turns=transcript,
                )
                history = self.runtime.turns if role == "caller" else self.sakinah_turns
                index = history.index(turn)
                enrich_scoring_turn(turn, result, history[index - 1] if index else None)
                await self.persist()
                if self._on_update:
                    await self._on_update(turn)
        except Exception as exc:  # scoring cannot interrupt audio
            logger.warning(
                "CALM evaluation failed for run {} turn {} ({}): {}",
                self.workflow_run_id, turn.get("turn_id"), role, type(exc).__name__,
            )

    async def wait_for_evaluations(self) -> None:
        if self._evaluation_tasks:
            _done, pending = await asyncio.wait(
                list(self._evaluation_tasks), timeout=25
            )
            for task in pending:
                task.cancel()
            if pending:
                logger.warning(
                    "CALM evaluation timeout for run {} ({} turns)",
                    self.workflow_run_id, len(pending),
                )

    def payload(self) -> dict[str, Any]:
        return {
            "version": 2,
            "workflow_run_id": self.workflow_run_id,
            "updated_at": datetime.now(UTC).isoformat(),
            "caller": self.runtime.turns,
            "sakinah": self.sakinah_turns,
        }

    async def persist(self) -> None:
        async with self._persist_lock:
            payload = self.payload()
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
                await persist_calm_scoring_artifact(
                    self.workflow_run_id, payload, replicate=True
                )
            except Exception as exc:  # object persistence is best effort during live audio
                logger.warning(
                    "CALM object persistence failed for run {}: {}",
                    self.workflow_run_id,
                    type(exc).__name__,
                )
