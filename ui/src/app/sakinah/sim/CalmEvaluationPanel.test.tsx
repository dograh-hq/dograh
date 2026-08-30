import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CalmEvaluationPanel } from "./CalmEvaluationPanel";
import type { InferredParameter, SakinahEvaluationResult } from "./calmTypes";

const parameter: InferredParameter = {
    score: 7,
    confidence: 8,
    evidence: "Supported by the response.",
};

const sakinahResult: SakinahEvaluationResult = {
    role: "sakinah",
    turn_id: "turn-1",
    response_quality: {
        empathy: parameter,
        emotional_attunement: parameter,
        validation: parameter,
    },
    safety_evaluation: {
        risk_recognition: parameter,
        clarification_quality: parameter,
        escalation_appropriateness: parameter,
        emergency_response_appropriateness: parameter,
        safeguarding_response: parameter,
        avoids_unsafe_reassurance: parameter,
        avoids_harmful_advice: parameter,
        proportionality: parameter,
        safety_plan_relevance: parameter,
        critical_flags: ["Immediate safeguarding danger was not addressed"],
    },
    clinical_evaluation: {
        summary: "The response was empathic but missed an immediate safety issue.",
        strengths: ["Empathic acknowledgement"],
        missed_opportunities: ["Direct safety clarification"],
        recommended_next_approach: ["Clarify immediate safety"],
    },
    evaluated_at: "2026-08-30T00:00:00Z",
};

describe("CalmEvaluationPanel", () => {
    it("shows a non-blocking pending state", () => {
        render(<CalmEvaluationPanel evaluation={{ status: "pending" }} />);
        expect(screen.getByText("Scoring...")).toBeTruthy();
    });

    it("shows evaluator failure without a transcript error", () => {
        render(<CalmEvaluationPanel evaluation={{ status: "failed" }} />);
        expect(screen.getByText("Evaluation unavailable")).toBeTruthy();
    });

    it("keeps critical safety flags prominent", () => {
        render(<CalmEvaluationPanel evaluation={{ status: "completed", result: sakinahResult }} />);
        expect(screen.getByText("Critical safety flags")).toBeTruthy();
        expect(screen.getByText("Immediate safeguarding danger was not addressed")).toBeTruthy();
    });
});

