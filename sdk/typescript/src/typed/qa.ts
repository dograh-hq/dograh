// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// `api/services/workflow/node_specs/` directory.


/**
 * Run LLM quality analysis on the call transcript.
 *
 * LLM hint: Runs an LLM quality review on the call transcript after completion. Per-node analysis splits the conversation by node and evaluates each segment against the configured system prompt. Sampling, minimum duration, and voicemail filters are supported.
 */
export interface Qa {
    type: "qa";
    /**
     * Short identifier for this QA configuration.
     */
    name?: string;
    /**
     * When false, the QA run is skipped.
     */
    qa_enabled?: boolean;
    /**
     * Instructions to the QA reviewer LLM. Supports placeholders: `{node_summary}`, `{previous_conversation_summary}`, `{transcript}`, `{metrics}`.
     */
    qa_system_prompt?: string;
    /**
     * Calls shorter than this are skipped.
     */
    qa_min_call_duration?: number;
    /**
     * When false, calls flagged as voicemail are skipped.
     */
    qa_voicemail_calls?: boolean;
    /**
     * Percent of eligible calls QA'd. 100 means every call; lower values use random sampling.
     */
    qa_sample_rate?: number;
    /**
     * When true, the QA pass uses the same LLM the workflow runs with. Set false to specify a separate provider/model.
     */
    qa_use_workflow_llm?: boolean;
    /**
     * LLM provider used for the QA pass.
     */
    qa_provider?: "openai" | "azure" | "openrouter" | "anthropic";
    /**
     * Model identifier (e.g., 'gpt-4o', 'claude-sonnet-4-6'). Provider-specific.
     */
    qa_model?: string;
    /**
     * API key for the chosen provider.
     */
    qa_api_key?: string;
    /**
     * Required for the Azure provider.
     */
    qa_endpoint?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function qa(input: Omit<Qa, "type">): Qa {
    return { type: "qa", ...input };
}
