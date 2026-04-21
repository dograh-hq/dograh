// Shared shapes used by both generate and parse. Mirror the `ReactFlowDTO`
// wire format on the Python side (`api/services/workflow/dto.py`) and the
// node-spec JSON served by `/api/v1/node-types` / dumped by
// `python -m api.services.workflow.node_specs`.

export interface PropertyOption {
    value: string | number | boolean;
    label: string;
}

export interface PropertySpec {
    name: string;
    type: string;
    required?: boolean;
    default?: unknown;
    options?: PropertyOption[];
    properties?: PropertySpec[];
}

export interface NodeSpec {
    name: string;
    properties: PropertySpec[];
}

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

export interface ParseErrorItem {
    message: string;
    line?: number;
    column?: number;
}

export type GenerateResult =
    | { ok: true; code: string }
    | { ok: false; errors: ParseErrorItem[] };

export type ParseResult =
    | { ok: true; workflow: WireWorkflow; workflowName: string }
    | { ok: false; stage: "parse" | "validate"; errors: ParseErrorItem[] };
