// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// `api/services/workflow/node_specs/` directory.


/**
 * Public HTTP endpoint that launches the workflow.
 *
 * LLM hint: Exposes a public HTTP POST endpoint. External systems call the URL (derived from the auto-generated `trigger_path`) to launch this workflow. Requires an API key in the `X-API-Key` header.
 */
export interface Trigger {
    type: "trigger";
    /**
     * Short identifier shown in the canvas. No runtime effect.
     */
    name?: string;
    /**
     * When false, the trigger URL returns 404.
     */
    enabled?: boolean;
    /**
     * Auto-generated UUID-style path segment that uniquely identifies this trigger. Do not edit manually.
     */
    trigger_path?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function trigger(input: Omit<Trigger, "type">): Trigger {
    return { type: "trigger", ...input };
}
