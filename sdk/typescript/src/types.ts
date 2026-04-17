// Structural types mirroring the NodeSpec schema served by the Dograh
// backend at /api/v1/node-types. Kept local (no dependency on the UI's
// generated client) so this package is self-contained and publishable.

export type PropertyType =
    | "string"
    | "number"
    | "boolean"
    | "options"
    | "multi_options"
    | "fixed_collection"
    | "json"
    | "tool_refs"
    | "document_refs"
    | "recording_ref"
    | "credential_ref"
    | "mention_textarea"
    | "url";

export interface PropertyOption {
    value: string | number | boolean;
    label: string;
    description?: string | null;
}

export interface DisplayOptions {
    show?: Record<string, unknown[]> | null;
    hide?: Record<string, unknown[]> | null;
}

export interface PropertySpec {
    name: string;
    type: PropertyType;
    display_name: string;
    description: string;
    llm_hint?: string | null;
    default?: unknown;
    required?: boolean;
    placeholder?: string | null;
    display_options?: DisplayOptions | null;
    options?: PropertyOption[] | null;
    properties?: PropertySpec[] | null;
    min_value?: number | null;
    max_value?: number | null;
    min_length?: number | null;
    max_length?: number | null;
    pattern?: string | null;
    editor?: string | null;
    extra?: Record<string, unknown>;
}

export type NodeCategory =
    | "call_node"
    | "global_node"
    | "trigger"
    | "integration";

export interface NodeSpec {
    name: string;
    display_name: string;
    description: string;
    llm_hint?: string | null;
    category: NodeCategory;
    icon: string;
    version: string;
    properties: PropertySpec[];
    examples?: Array<{
        name: string;
        description?: string | null;
        data: Record<string, unknown>;
    }>;
    // migrations and graph_constraints exist on the wire but aren't
    // needed for the SDK's client-side validation — intentionally omitted.
}

/** Opaque handle returned by `Workflow.add()` and passed to `edge()`. */
export interface NodeRef {
    id: string;
    type: string;
}

/** Wire-format shapes matching `ReactFlowDTO` in the backend. */
export interface WireNode {
    id: string;
    type: string;
    position: { x: number; y: number };
    data: Record<string, unknown>;
}

export interface WireEdge {
    id: string;
    source: string;
    target: string;
    data: Record<string, unknown>;
}

export interface WireWorkflow {
    nodes: WireNode[];
    edges: WireEdge[];
    viewport: { x: number; y: number; zoom: number };
}
