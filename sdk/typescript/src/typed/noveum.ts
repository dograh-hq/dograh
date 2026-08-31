// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.


/**
 * Export the completed call to Noveum for tracing and evaluation
 *
 * LLM hint: Noveum is a post-call observability export. It does not participate in the conversation graph and should not be connected to other nodes.
 */
export interface Noveum {
    type: "noveum";
    /**
     * Short identifier for this Noveum export configuration.
     */
    name?: string;
    /**
     * When false, Dograh skips exporting this call to Noveum.
     */
    noveum_enabled?: boolean;
    /**
     * Bearer token used when exporting the completed call to Noveum.
     */
    noveum_api_key: string;
    /**
     * The Noveum project the call trace is exported into.
     */
    noveum_project: string;
    /**
     * Environment label stamped on exported traces (e.g. production, staging).
     */
    noveum_environment?: string;
    /**
     * Capture per-segment STT/TTS audio and the full-conversation recording for audio evaluation on Noveum.
     */
    noveum_record_audio?: boolean;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function noveum(input: Omit<Noveum, "type">): Noveum {
    return { type: "noveum", ...input };
}
