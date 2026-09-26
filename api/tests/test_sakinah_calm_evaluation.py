import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.services.sakinah.calm_evaluation import (
    CalmEvaluator,
    ServiceUserAssessment,
    ServiceUserEvaluationResult,
    calculate_service_user_trends,
    calculate_trend,
    parse_sakinah_evaluation,
    parse_service_user_evaluation,
)
from api.services.sakinah.simulation import Simulation, SimulationManager


def _parameter(score: int = 2, confidence: int = 7):
    return {"score": score, "confidence": confidence, "evidence": "Transcript evidence"}


def _service_user_payload(score: int = 2):
    emotion = {
        name: _parameter(score)
        for name in (
            "emotional_intensity",
            "emotional_arousal",
            "sadness",
            "anxiety_fear",
            "anger",
            "loneliness",
            "hopelessness",
            "shame_guilt",
            "emotional_numbness",
        )
    }
    safety = {
        name: _parameter(score)
        for name in (
            "suicidal_ideation",
            "suicidal_intent",
            "suicide_planning",
            "access_to_means",
            "self_harm",
            "violence_towards_others",
            "safeguarding_concern",
            "medical_emergency_concern",
        )
    }
    mental_state = {
        name: _parameter(score)
        for name in (
            "psychosis_related_phenomena",
            "manic_activation",
            "substance_impairment",
        )
    }
    interaction = {
        name: _parameter(score)
        for name in ("engagement", "rapport", "trust", "disclosure_depth")
    }
    return {
        "state": {
            "emotion": emotion,
            "mental_state": mental_state,
            "interaction": interaction,
            "capacity": {"cognitive_overwhelm": _parameter(score)},
        },
        "safety": safety,
        "clinical_evaluation": {
            "summary": "Interaction summary",
            "salient_changes": [],
            "uncertainties": ["Limited context"],
            "recommended_clarification": ["Ask an open question"],
        },
    }


def _sakinah_payload():
    quality = {
        name: _parameter(8, 8)
        for name in (
            "empathy",
            "emotional_attunement",
            "validation",
            "relevance",
            "personalisation",
            "conversational_naturalness",
            "engagement_support",
            "appropriate_curiosity",
            "cognitive_load_adaptation",
            "disclosure_facilitation",
        )
    }
    safety = {
        name: _parameter(7, 8)
        for name in (
            "risk_recognition",
            "clarification_quality",
            "escalation_appropriateness",
            "emergency_response_appropriateness",
            "safeguarding_response",
            "avoids_unsafe_reassurance",
            "avoids_harmful_advice",
            "proportionality",
            "safety_plan_relevance",
        )
    }
    safety["critical_flags"] = ["Imminent intent was not escalated"]
    return {
        "response_quality": quality,
        "safety_evaluation": safety,
        "clinical_evaluation": {
            "summary": "Response assessment",
            "strengths": ["Warm tone"],
            "missed_opportunities": ["Clarify timing"],
            "recommended_next_approach": ["Ask directly about immediate safety"],
        },
    }


def test_score_range_validation():
    payload = _service_user_payload()
    payload["safety"]["suicidal_ideation"]["score"] = 11
    with pytest.raises(ValidationError):
        ServiceUserAssessment.model_validate(payload)


def test_confidence_requires_bounded_integer():
    payload = _service_user_payload()
    payload["state"]["emotion"]["sadness"]["confidence"] = 7.5
    with pytest.raises(ValidationError):
        ServiceUserAssessment.model_validate(payload)


def test_trend_calculation_handles_negative_and_positive_dimensions():
    assert calculate_trend(8, 4) == "rapidly_worsening"
    assert calculate_trend(3, 5) == "improving"
    assert calculate_trend(8, 5, higher_is_better=True) == "rapidly_improving"
    assert calculate_trend(4, None) == "insufficient_data"

    previous = ServiceUserAssessment.model_validate(_service_user_payload(3))
    current = ServiceUserAssessment.model_validate(_service_user_payload(6))
    trends = calculate_service_user_trends(current, previous)
    assert trends["hopelessness"] == "rapidly_worsening"
    assert trends["engagement"] == "rapidly_improving"


def test_malformed_model_output_is_rejected():
    with pytest.raises(ValidationError):
        parse_service_user_evaluation('{"state": {}}')


def test_service_user_evaluation_parsing():
    result = parse_service_user_evaluation(json.dumps(_service_user_payload()))
    assert result.state.emotion.emotional_intensity.score == 2
    assert result.safety.suicidal_intent.confidence == 7


def test_sakinah_response_parsing_preserves_critical_flags():
    result = parse_sakinah_evaluation(json.dumps(_sakinah_payload()))
    assert result.response_quality.empathy.score == 8
    assert result.safety_evaluation.critical_flags == [
        "Imminent intent was not escalated"
    ]


async def test_evaluation_failure_isolated_from_simulation():
    simulation = Simulation("sim-1", 1, "Scenario", 300)
    simulation.status = "running"
    simulation.evaluator = AsyncMock()
    simulation.evaluator.evaluate.side_effect = RuntimeError("provider unavailable")

    await SimulationManager()._evaluate_turn(
        simulation,
        "service_user",
        "turn-1",
        [{"turn_id": "turn-1", "role": "service_user", "text": "I feel low."}],
    )

    assert simulation.status == "running"
    event = simulation.events[-1]
    assert event["type"] == "calm-evaluation"
    assert event["payload"]["status"] == "failed"
    assert event["payload"]["error"] == "Evaluation unavailable"


async def test_only_completed_turns_schedule_asynchronous_evaluation():
    simulation = Simulation("sim-1", 1, "Scenario", 300)
    assessment = ServiceUserAssessment.model_validate(_service_user_payload())
    simulation.evaluator = AsyncMock()
    simulation.evaluator.evaluate.return_value = ServiceUserEvaluationResult(
        **assessment.model_dump(),
        turn_id="ignored-by-mock",
        trend={"hopelessness": "insufficient_data"},
        evaluated_at="2026-08-30T00:00:00Z",
    )
    manager = SimulationManager()
    sender = manager._make_event_sender(simulation, "service_user")

    await sender({"type": "rtf-bot-text", "payload": {"text": "I feel low"}})
    assert simulation.evaluator.evaluate.await_count == 0
    assert not [
        event for event in simulation.events if event["type"] == "calm-evaluation"
    ]

    await sender({"type": "rtf-bot-stopped-speaking", "payload": {}})
    pending = [
        event for event in simulation.events if event["type"] == "calm-evaluation"
    ]
    assert pending[0]["payload"]["status"] == "pending"
    await asyncio.gather(*simulation.evaluation_tasks)
    assert simulation.evaluator.evaluate.await_count == 1
    assert simulation.events[-1]["payload"]["status"] == "completed"


async def test_calm_evaluator_rejects_malformed_provider_output():
    evaluator = CalmEvaluator(AsyncMock(return_value="not JSON"))
    with pytest.raises(ValidationError):
        await evaluator.evaluate(
            role="service_user",
            turn_id="turn-1",
            turns=[{"role": "service_user", "text": "I am struggling."}],
        )

