// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// `api/services/workflow/node_specs/` directory.

/**
 * Each entry declares one variable to capture from the conversation, with its name, data type, and a per-variable extraction hint.
 */
export interface EndCallExtraction_variablesRow {
    /**
     * snake_case identifier used downstream.
     */
    name: string;
    /**
     * The data type of the extracted value.
     */
    type: "string" | "number" | "boolean";
    /**
     * Per-variable hint describing what to look for in the conversation.
     */
    prompt?: string;
}

/**
 * Closes the conversation and hangs up.
 *
 * LLM hint: Terminal node that politely closes the conversation. Variable extraction can run before hangup. A workflow can have multiple endCall nodes reached via different edge conditions.
 */
export interface EndCall {
    type: "endCall";
    /**
     * Short identifier shown in call logs. Should describe the ending context (e.g., 'Successful close', 'Polite decline').
     */
    name?: string;
    /**
     * Agent system prompt for the closing exchange. Supports {{template_variables}} from extraction or pre-call fetch.
     */
    prompt: string;
    /**
     * When true and a Global node exists, prepends the global prompt to this node's prompt at runtime.
     */
    add_global_prompt?: boolean;
    /**
     * When true, runs an LLM extraction pass before hangup to capture variables from the conversation.
     */
    extraction_enabled?: boolean;
    /**
     * Overall instructions guiding how variables should be extracted from the conversation.
     */
    extraction_prompt?: string;
    /**
     * Each entry declares one variable to capture from the conversation, with its name, data type, and a per-variable extraction hint.
     */
    extraction_variables?: Array<EndCallExtraction_variablesRow>;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function endCall(input: Omit<EndCall, "type">): EndCall {
    return { type: "endCall", ...input };
}
