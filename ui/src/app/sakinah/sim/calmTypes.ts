export interface InferredParameter {
    score: number;
    confidence: number;
    evidence: string;
}

export type ParameterGroup = Record<string, InferredParameter>;
export type Trend =
    | "rapidly_improving"
    | "improving"
    | "stable"
    | "worsening"
    | "rapidly_worsening"
    | "insufficient_data";

export interface ServiceUserEvaluationResult {
    role: "service_user";
    turn_id: string;
    state: {
        emotion: ParameterGroup;
        mental_state: ParameterGroup;
        interaction: ParameterGroup;
        capacity: ParameterGroup;
    };
    safety: ParameterGroup;
    trend: Record<string, Trend>;
    clinical_evaluation: {
        summary: string;
        salient_changes: string[];
        uncertainties: string[];
        recommended_clarification: string[];
    };
    evaluated_at: string;
}

export interface SakinahEvaluationResult {
    role: "sakinah";
    turn_id: string;
    response_quality: ParameterGroup;
    safety_evaluation: {
        risk_recognition: InferredParameter;
        clarification_quality: InferredParameter;
        escalation_appropriateness: InferredParameter;
        emergency_response_appropriateness: InferredParameter;
        safeguarding_response: InferredParameter;
        avoids_unsafe_reassurance: InferredParameter;
        avoids_harmful_advice: InferredParameter;
        proportionality: InferredParameter;
        safety_plan_relevance: InferredParameter;
        critical_flags: string[];
    };
    clinical_evaluation: {
        summary: string;
        strengths: string[];
        missed_opportunities: string[];
        recommended_next_approach: string[];
    };
    evaluated_at: string;
}

export type CalmEvaluationResult =
    | ServiceUserEvaluationResult
    | SakinahEvaluationResult;

export interface TurnEvaluation {
    status: "pending" | "completed" | "failed";
    result?: CalmEvaluationResult;
    error?: string;
}

