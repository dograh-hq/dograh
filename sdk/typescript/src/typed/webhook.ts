// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// `api/services/workflow/node_specs/` directory.

/**
 * Additional HTTP headers to include with the request.
 */
export interface WebhookCustom_headersRow {
    /**
     * HTTP header name (e.g., 'X-Source').
     */
    key: string;
    /**
     * Header value (supports {{template_variables}}).
     */
    value: string;
}

/**
 * Send HTTP request after the workflow completes.
 *
 * LLM hint: Sends an HTTP request to an external system after the workflow completes. The payload is a Jinja-templated JSON body with access to `workflow_run_id`, `initial_context`, `gathered_context`, `annotations`, and call metadata.
 */
export interface Webhook {
    type: "webhook";
    /**
     * Short identifier shown in the canvas and run logs.
     */
    name?: string;
    /**
     * When false, the webhook is skipped at run time.
     */
    enabled?: boolean;
    /**
     * HTTP verb used for the outbound request.
     */
    http_method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
    /**
     * URL the request is sent to.
     */
    endpoint_url?: string;
    /**
     * Optional credential applied as the Authorization header.
     *
     * LLM hint: Credential UUID from `list_credentials`.
     */
    credential_uuid?: string;
    /**
     * Additional HTTP headers to include with the request.
     */
    custom_headers?: Array<WebhookCustom_headersRow>;
    /**
     * JSON body of the request. Values are Jinja-rendered against the run context — `{{workflow_run_id}}`, `{{gathered_context.foo}}`, `{{annotations.qa_xxx}}`, etc.
     */
    payload_template?: Record<string, unknown>;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function webhook(input: Omit<Webhook, "type">): Webhook {
    return { type: "webhook", ...input };
}
