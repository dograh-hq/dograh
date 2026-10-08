// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.


/**
 * Send the completed call to Roark for transcription, scoring and analytics
 *
 * LLM hint: Roark is a post-call analytics export. It does not participate in the conversation graph and should not be connected to other nodes. The call recording must be reachable from the public internet for Roark to ingest it.
 */
export interface Roark {
    type: "roark";
    /**
     * Short identifier for this Roark export configuration.
     */
    name?: string;
    /**
     * When false, Dograh skips exporting this call to Roark.
     */
    roark_enabled?: boolean;
    /**
     * Project API key used to post completed calls to Roark.
     */
    roark_api_key: string;
    /**
     * Name of the agent in Roark. An agent with this exact name in the project is reused; otherwise Roark creates one.
     */
    roark_agent_name?: string;
    /**
     * Optional. UUID of an existing Roark agent. Takes precedence over the agent name when both are set.
     */
    roark_agent_id?: string;
    /**
     * Send the transcript and tool calls Dograh captured during the call. Roark grades the transcript you send in preference to its own. Turn this off to have Roark grade its own transcription of the recording instead; it then has no record of the tool calls either.
     */
    roark_send_transcript?: boolean;
    /**
     * Also send the variables the agent gathered during the call as Roark call properties, so you can filter on them. Off by default because gathered context often holds personal data.
     */
    roark_send_gathered_context?: boolean;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function roark(input: Omit<Roark, "type">): Roark {
    return { type: "roark", ...input };
}
