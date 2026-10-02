// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.


/**
 * Let the agent search a Moss index during the call
 *
 * LLM hint: Moss is a knowledge retrieval configuration node. It does not participate in the conversation graph and should not be connected to other nodes. When enabled, every Start Call and Agent node gets a search_moss_index tool that searches the configured Moss index.
 */
export interface Moss {
    type: "moss";
    /**
     * Short identifier for this Moss index configuration.
     */
    name?: string;
    /**
     * When false, agents do not get the Moss search tool.
     */
    moss_enabled?: boolean;
    /**
     * Name of the Moss index the agent searches.
     */
    moss_index_name: string;
    /**
     * Moss project that owns the index.
     */
    moss_project_id: string;
    /**
     * Moss project key used to download the index.
     */
    moss_project_key: string;
    /**
     * What the index contains. The agent reads this to decide when to search.
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
