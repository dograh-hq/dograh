// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.


/**
 * Export the completed call to Tuner for Agent Observability
 *
 * LLM hint: Tuner is a post-call observability export. It does not participate in the conversation graph and should not be connected to other nodes.
 */
export interface Tuner {
    type: "tuner";
    /**
     * Short identifier for this Tuner export configuration.
     */
    name?: string;
    /**
     * When false, Dograh skips exporting this call to Tuner.
     */
    tuner_enabled?: boolean;
    /**
     * The agent identifier registered in your Tuner workspace.
     */
    tuner_agent_id: string;
    /**
     * Your numeric Tuner workspace ID.
     */
    tuner_workspace_id: number;
    /**
     * Bearer token used when posting completed calls to Tuner.
     */
    tuner_api_key: string;
    /**
     * Send a per-call cost to Tuner, computed from your own provider rates (BYOK). All rates below are optional.
     */
    cost_calculation_enabled?: boolean;
    /**
     * USD per 1M tokens
     */
    cost_llm_input_rate?: number;
    /**
     * USD per 1M cached tokens
     */
    cost_llm_cached_input_rate?: number;
    /**
     * USD per 1M tokens
     */
    cost_llm_output_rate?: number;
    /**
     * USD per 1K characters
     */
    cost_tts_rate?: number;
    /**
     * USD per minute
     */
    cost_stt_rate?: number;
    /**
     * USD per minute
     */
    cost_telephony_rate?: number;
    /**
     * Send selected variables to Tuner as call metadata, so Tuner evals, rules and alerts can use them.
     */
    metadata_enabled?: boolean;
    /**
     * Variables from the call's initial context (including Pre-Call Data Fetch) or extracted variables. Separate names with commas or new lines. Variables you don't list are never sent.
     */
    metadata_keys?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function tuner(input: Omit<Tuner, "type">): Tuner {
    return { type: "tuner", ...input };
}
