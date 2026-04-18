// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// `api/services/workflow/node_specs/` directory.

/**
 * Each entry declares one variable to capture, with its name, data type, and per-variable extraction hint.
 */
export interface StartCallExtraction_variablesRow {
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
 * Entry point of the workflow — plays a greeting and opens the conversation.
 *
 * LLM hint: The entry point of every workflow (exactly one required). Plays an optional greeting, can fetch context from an external API before the call begins, and executes the first conversational turn.
 */
export interface StartCall {
    type: "startCall";
    /**
     * Short identifier shown in the canvas and call logs.
     */
    name?: string;
    /**
     * Agent system prompt for the opening turn. Supports {{template_variables}} from pre-call fetch and the initial context.
     */
    prompt: string;
    /**
     * Whether the optional greeting is spoken via TTS from text or played from a pre-recorded audio file.
     */
    greeting_type?: "text" | "audio";
    /**
     * Text spoken via TTS at the start of the call. Supports {{template_variables}}. Leave empty to skip the greeting.
     */
    greeting?: string;
    /**
     * Pre-recorded audio file played at the start of the call.
     *
     * LLM hint: Value is the `recording_id` string. Use the `list_recordings` MCP tool to discover available recordings.
     */
    greeting_recording_id?: string;
    /**
     * When true, the user can interrupt the agent mid-utterance.
     */
    allow_interrupt?: boolean;
    /**
     * When true and a Global node exists, prepends the global prompt to this node's prompt at runtime.
     */
    add_global_prompt?: boolean;
    /**
     * When true, the agent waits before speaking after pickup. Useful for outbound calls where the called party needs a moment to settle.
     */
    delayed_start?: boolean;
    /**
     * Seconds to wait before the agent speaks. 0.1–10.
     */
    delayed_start_duration?: number;
    /**
     * When true, runs an LLM extraction pass on transition out of this node to capture variables from the opening turn.
     */
    extraction_enabled?: boolean;
    /**
     * Overall instructions guiding variable extraction.
     */
    extraction_prompt?: string;
    /**
     * Each entry declares one variable to capture, with its name, data type, and per-variable extraction hint.
     */
    extraction_variables?: Array<StartCallExtraction_variablesRow>;
    /**
     * Tools the agent can invoke during the opening turn.
     *
     * LLM hint: List of tool UUIDs from `list_tools`.
     */
    tool_uuids?: string[];
    /**
     * Documents the agent can reference.
     *
     * LLM hint: List of document UUIDs from `list_documents`.
     */
    document_uuids?: string[];
    /**
     * When true, makes a POST request to an external API before the call starts and merges the JSON response into the call context as template variables.
     */
    pre_call_fetch_enabled?: boolean;
    /**
     * URL the pre-call POST request is sent to. The request body includes caller and called numbers.
     */
    pre_call_fetch_url?: string;
    /**
     * Optional credential attached to the pre-call request.
     *
     * LLM hint: Credential UUID from `list_credentials`.
     */
    pre_call_fetch_credential_uuid?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function startCall(input: Omit<StartCall, "type">): StartCall {
    return { type: "startCall", ...input };
}
