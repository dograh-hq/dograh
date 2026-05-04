// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// `api/services/workflow/node_specs/` directory.


/**
 * Public HTTP endpoints that launch the workflow.
 *
 * LLM hint: Exposes two public HTTP POST endpoints derived from the auto-generated `trigger_path`:
 *   • Production: `<backend>/api/v1/public/agent/<trigger_path>` — runs the published agent. Use this from production systems.
 *   • Test: `<backend>/api/v1/public/agent/test/<trigger_path>` — runs the latest draft, useful for verifying changes before publishing. Falls back to the published agent when no draft exists.
 * Both require an API key in the `X-API-Key` header.
 * Request body fields:
 *   • `phone_number` (string, required) — destination to dial.
 *   • `initial_context` (object, optional) — merged into the run's initial context.
 *   • `telephony_configuration_id` (int, optional) — pick a specific telephony configuration for the call. Must belong to the same organization as the trigger. When omitted, the org's default outbound configuration is used.
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
     * Auto-generated UUID-style path segment that uniquely identifies this trigger. Used in both URLs:
     *   • Production: `/api/v1/public/agent/<trigger_path>` — executes the published agent.
     *   • Test: `/api/v1/public/agent/test/<trigger_path>` — executes the latest draft.
     * Do not edit manually.
     */
    trigger_path?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function trigger(input: Omit<Trigger, "type">): Trigger {
    return { type: "trigger", ...input };
}
