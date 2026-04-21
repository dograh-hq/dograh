// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// `api/services/workflow/node_specs/` directory.

/**
 * Each entry declares one variable to capture from the conversation, with its name, type, and per-variable hint.
 */
export interface AgentNodeExtraction_variablesRow {
    /**
     * snake_case identifier used downstream.
     */
    name: string;
    /**
     * Data type of the extracted value.
     */
    type: "string" | "number" | "boolean";
    /**
     * Per-variable hint describing what to look for.
     */
    prompt?: string;
}

/**
 * Conversational step — the LLM runs one focused exchange.
 *
 * LLM hint: Mid-call step executed by the LLM. Most workflows are a chain of agent nodes connected by edges that describe transition conditions. Each agent node can invoke tools and reference documents.
 */
export interface AgentNode {
    type: "agentNode";
    /**
     * Short identifier for this step (e.g., 'Qualify Budget'). Appears in call logs and edge transition tools.
     */
    name?: string;
    /**
     * Agent system prompt for this step. Supports {{template_variables}} from extraction or pre-call fetch.
     */
    prompt: string;
    /**
     * When true, the user can interrupt the agent mid-utterance. Set false for non-interruptible disclosures.
     */
    allow_interrupt?: boolean;
    /**
     * When true and a Global node exists, prepends the global prompt to this node's prompt at runtime.
     */
    add_global_prompt?: boolean;
    /**
     * When true, runs an LLM extraction pass on transition out of this node to capture variables from the conversation.
     */
    extraction_enabled?: boolean;
    /**
     * Overall instructions guiding variable extraction.
     */
    extraction_prompt?: string;
    /**
     * Each entry declares one variable to capture from the conversation, with its name, type, and per-variable hint.
     */
    extraction_variables?: Array<AgentNodeExtraction_variablesRow>;
    /**
     * Tools the agent can invoke during this step.
     *
     * LLM hint: List of tool UUIDs from `list_tools`.
     */
    tool_uuids?: string[];
    /**
     * Documents the agent can reference during this step.
     *
     * LLM hint: List of document UUIDs from `list_documents`.
     */
    document_uuids?: string[];
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function agentNode(input: Omit<AgentNode, "type">): AgentNode {
    return { type: "agentNode", ...input };
}
