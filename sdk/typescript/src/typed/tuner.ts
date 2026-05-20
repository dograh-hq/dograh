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
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function tuner(input: Omit<Tuner, "type">): Tuner {
    return { type: "tuner", ...input };
}
