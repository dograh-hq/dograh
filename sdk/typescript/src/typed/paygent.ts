// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.


/**
 * Cost Tracking and Billing
 *
 * LLM hint: Paygent is a post-call usage-tracking and billing integration. It does not participate in the conversation graph and should not be connected to other nodes.
 */
export interface Paygent {
    type: "paygent";
    /**
     * Short identifier for this Paygent configuration.
     */
    name?: string;
    /**
     * When false, Dograh skips all Paygent tracking for this call.
     */
    paygent_enabled?: boolean;
    /**
     * API key used to authenticate requests to the Paygent REST API.
     */
    paygent_api_key: string;
    /**
     * The agent identifier registered in your Paygent account.
     */
    paygent_agent_id: string;
    /**
     * Your Paygent customer / organisation ID.
     */
    paygent_customer_id: string;
    /**
     * The indicator event name sent at the end of the call (e.g. per-minute-call).
     */
    paygent_indicator?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function paygent(input: Omit<Paygent, "type">): Paygent {
    return { type: "paygent", ...input };
}
