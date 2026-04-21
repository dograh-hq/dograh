// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// `api/services/workflow/node_specs/` directory.


/**
 * Persona/tone appended to every agent node's prompt.
 *
 * LLM hint: System-level prompt appended to every prompted node whose `add_global_prompt` is true. Use it for persona, tone, and shared rules that apply across the entire conversation. At most one global node per workflow.
 */
export interface GlobalNode {
    type: "globalNode";
    /**
     * Short identifier shown in the canvas and call logs. Has no runtime effect.
     */
    name?: string;
    /**
     * Text appended to every prompted node's system prompt when that node has `add_global_prompt=true`. Supports {{template_variables}}.
     */
    prompt?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function globalNode(input: Omit<GlobalNode, "type">): GlobalNode {
    return { type: "globalNode", ...input };
}
