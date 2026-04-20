// Workflow builder mirroring `sdk/python/src/dograh_sdk/workflow.py`.
//
// Users compose workflows via `workflow.add({ type: "agentNode", ... })`
// and `workflow.edge(source, target, ...)`. Each `add()` call is
// validated against the fetched spec immediately, so LLM hallucinations
// fail at the call site rather than at save time.
//
// Wire format matches `ReactFlowDTO` from the backend 1:1 — `toJson()`
// output round-trips through `ReactFlowDTO.model_validate` unchanged.

import type { NodeSpec } from "./_generated_models.js";
import { ValidationError } from "./errors.js";
import type { NodeRef, WireEdge, WireNode, WireWorkflow } from "./types.js";
import { validateNodeData } from "./validation.js";

/** Minimal interface the Workflow builder needs from a client. Any object
 * satisfying this shape works (real HTTP client, in-memory stub, etc.). */
export interface SpecProvider {
    getNodeType(name: string): Promise<NodeSpec>;
}

export interface WorkflowOptions {
    client: SpecProvider;
    name?: string;
    description?: string;
}

export interface AddNodeOptions {
    type: string;
    position?: [number, number];
    /** Remaining node data fields are validated against the spec. */
    [key: string]: unknown;
}

export interface EdgeOptions {
    label: string;
    condition: string;
    transitionSpeech?: string;
    transitionSpeechType?: "text" | "audio";
    transitionSpeechRecordingId?: string;
}

export class Workflow {
    readonly name: string;
    readonly description: string;
    private readonly client: SpecProvider;
    private readonly nodes: WireNode[] = [];
    private readonly edges: WireEdge[] = [];
    // Auto-incrementing IDs match the pattern used by the existing UI.
    private nextNodeId = 1;

    constructor(opts: WorkflowOptions) {
        this.client = opts.client;
        this.name = opts.name ?? "";
        this.description = opts.description ?? "";
    }

    /**
     * Add a node of the given type.
     *
     * `type` is a spec name (e.g., "startCall", "agentNode"). Remaining
     * properties are validated against the spec — unknown or missing
     * required fields throw `ValidationError` immediately.
     */
    async add(opts: AddNodeOptions): Promise<NodeRef> {
        const { type, position, ...rest } = opts;
        const spec = await this.client.getNodeType(type);
        const data = validateNodeData(spec, rest);

        const nodeId = String(this.nextNodeId++);
        const [x, y] = position ?? [0, 0];
        this.nodes.push({
            id: nodeId,
            type,
            position: { x, y },
            data,
        });
        return { id: nodeId, type };
    }

    /**
     * Typed variant of `add()` — takes a typed node object from
     * `@dograh/sdk/typed` (or its discriminated-union form) instead of
     * raw kwargs.
     *
     * Equivalent to:
     *   const { type, ...rest } = node;
     *   wf.add({ type, position, ...rest });
     *
     * Benefits: TS narrows the allowed fields per `type` at edit time,
     * and IDEs surface the spec's description + llm_hint as JSDoc.
     */
    async addTyped<T extends { type: string }>(
        node: T,
        opts?: { position?: [number, number] },
    ): Promise<NodeRef> {
        const { type, ...rest } = node as unknown as { type: string } & Record<
            string,
            unknown
        >;
        return this.add({ type, position: opts?.position, ...rest });
    }

    /**
     * Connect two nodes with a labeled transition.
     *
     * `label` identifies the branch in call logs and LLM tool schemas;
     * `condition` is the natural-language predicate the engine evaluates
     * to decide when to follow the edge.
     */
    edge(source: NodeRef, target: NodeRef, opts: EdgeOptions): void {
        if (!opts.label || opts.label.trim() === "") {
            throw new ValidationError("edge.label is required");
        }
        if (!opts.condition || opts.condition.trim() === "") {
            throw new ValidationError("edge.condition is required");
        }

        const data: Record<string, unknown> = {
            label: opts.label,
            condition: opts.condition,
        };
        if (opts.transitionSpeech !== undefined) {
            data.transition_speech = opts.transitionSpeech;
        }
        if (opts.transitionSpeechType !== undefined) {
            data.transition_speech_type = opts.transitionSpeechType;
        }
        if (opts.transitionSpeechRecordingId !== undefined) {
            data.transition_speech_recording_id = opts.transitionSpeechRecordingId;
        }

        this.edges.push({
            id: `${source.id}-${target.id}`,
            source: source.id,
            target: target.id,
            data,
        });
    }

    /** Serialize to the `ReactFlowDTO` wire format. */
    toJson(): WireWorkflow {
        return {
            nodes: this.nodes.map((n) => ({ ...n, position: { ...n.position }, data: { ...n.data } })),
            edges: this.edges.map((e) => ({ ...e, data: { ...e.data } })),
            viewport: { x: 0, y: 0, zoom: 1 },
        };
    }

    /**
     * Rebuild a Workflow from a stored `workflow_json` payload. Useful
     * for the "view/edit as code" flow: fetch existing workflow, convert
     * to SDK objects, let the LLM mutate in code, serialize back.
     */
    static async fromJson(
        payload: { nodes?: WireNode[]; edges?: WireEdge[] } & Record<string, unknown>,
        opts: WorkflowOptions,
    ): Promise<Workflow> {
        const wf = new Workflow(opts);
        for (const raw of payload.nodes ?? []) {
            const spec = await wf.client.getNodeType(raw.type);
            const validated = validateNodeData(spec, raw.data ?? {});
            wf.nodes.push({
                id: String(raw.id),
                type: raw.type,
                position: raw.position ?? { x: 0, y: 0 },
                data: validated,
            });
        }
        // Keep ID generator above the highest numeric ID seen so new
        // nodes don't collide with existing ones.
        const numericIds = wf.nodes
            .map((n) => Number(n.id))
            .filter((n) => Number.isInteger(n));
        wf.nextNodeId = (numericIds.length > 0 ? Math.max(...numericIds) : 0) + 1;

        for (const raw of payload.edges ?? []) {
            wf.edges.push({
                id: String(raw.id ?? `${raw.source}-${raw.target}`),
                source: String(raw.source),
                target: String(raw.target),
                data: raw.data ?? {},
            });
        }
        return wf;
    }

    /** Find a NodeRef by ID. Useful after `fromJson` to reference
     * pre-existing nodes when building new edges. */
    findNode(id: string): NodeRef | null {
        const found = this.nodes.find((n) => n.id === id);
        return found ? { id: found.id, type: found.type } : null;
    }
}
