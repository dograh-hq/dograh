// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.


/**
 * Let the agent search a Moss index during the call
 *
 * LLM hint: Moss is a knowledge retrieval configuration node. It does not participate in the conversation graph and should not be connected to other nodes. When enabled, Start Call and Agent nodes search the configured Moss index: in ambient mode on every caller turn before the LLM answers, in tool mode through a search_moss_index tool the LLM calls.
 */
export interface Moss {
    type: "moss";
    /**
     * Short identifier for this Moss index configuration.
     */
    name?: string;
    /**
     * When false, agents do not search the Moss index.
     */
    moss_enabled?: boolean;
    /**
     * When the agent searches the index.
     */
    moss_mode?: "ambient" | "tool";
    /**
     * Name of the Moss index the agent searches.
     */
    moss_index_name?: string;
    /**
     * Moss project that owns the index.
     */
    moss_project_id?: string;
    /**
     * Moss project key used to download the index.
     */
    moss_project_key?: string;
    /**
     * What the index contains. With the search tool, which tool mode and speech to speech calls use, the agent reads this to decide when to search.
     */
    moss_index_description?: string;
    /**
     * How many documents each search returns to the agent.
     */
    moss_top_k?: number;
    /**
     * Blend of semantic and keyword matching: 1.0 is semantic only, 0.0 is keyword only.
     */
    moss_alpha?: number;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function moss(input: Omit<Moss, "type">): Moss {
    return { type: "moss", ...input };
}
