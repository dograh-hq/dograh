// HTTP client for the Dograh REST API. Wraps `/api/v1/node-types`, the
// reference-catalog endpoints, and the workflow CRUD endpoints. Specs are
// fetched once per client and cached in memory.

import { ApiError, SpecMismatchError } from "./errors.js";
import type { NodeSpec } from "./types.js";
import { Workflow, type SpecProvider } from "./workflow.js";

export interface DograhClientOptions {
    baseUrl?: string;
    apiKey?: string;
    /** Request timeout in ms. */
    timeoutMs?: number;
    /** Optional fetch override for tests / custom transports. */
    fetch?: typeof globalThis.fetch;
}

export class DograhClient implements SpecProvider {
    readonly baseUrl: string;
    readonly apiKey: string | undefined;
    private readonly fetchImpl: typeof globalThis.fetch;
    private readonly timeoutMs: number;
    private readonly headers: Record<string, string>;
    private readonly specCache = new Map<string, NodeSpec>();
    private specVersionCache: string | null = null;

    constructor(opts: DograhClientOptions = {}) {
        const rawBase =
            opts.baseUrl ??
            (typeof process !== "undefined" ? process.env.DOGRAH_API_URL : undefined) ??
            "http://localhost:8000";
        this.baseUrl = rawBase.replace(/\/+$/, "");
        this.apiKey =
            opts.apiKey ??
            (typeof process !== "undefined" ? process.env.DOGRAH_API_KEY : undefined);
        this.fetchImpl = opts.fetch ?? globalThis.fetch;
        this.timeoutMs = opts.timeoutMs ?? 30_000;
        this.headers = { Accept: "application/json" };
        if (this.apiKey) this.headers["X-API-Key"] = this.apiKey;
    }

    // ── spec discovery ─────────────────────────────────────────────

    async listNodeTypes(): Promise<{
        spec_version: string;
        node_types: NodeSpec[];
    }> {
        const body = await this.request<{
            spec_version: string;
            node_types: NodeSpec[];
        }>("GET", "/node-types");
        this.specVersionCache = body.spec_version;
        for (const spec of body.node_types ?? []) {
            this.specCache.set(spec.name, spec);
        }
        return body;
    }

    async getNodeType(name: string): Promise<NodeSpec> {
        const cached = this.specCache.get(name);
        if (cached) return cached;
        try {
            const spec = await this.request<NodeSpec>("GET", `/node-types/${name}`);
            this.specCache.set(name, spec);
            return spec;
        } catch (err) {
            if (err instanceof ApiError && err.statusCode === 404) {
                throw new SpecMismatchError(`Unknown node type: ${JSON.stringify(name)}`);
            }
            throw err;
        }
    }

    /** Spec contract version reported by the server, or null until first
     * discovery call. */
    get specVersion(): string | null {
        return this.specVersionCache;
    }

    // ── reference catalogs ─────────────────────────────────────────

    async listTools(): Promise<Array<Record<string, unknown>>> {
        return this.request("GET", "/tools/");
    }

    async listDocuments(): Promise<Array<Record<string, unknown>>> {
        const body = await this.request<unknown>("GET", "/knowledge-base/documents");
        if (body && typeof body === "object" && "documents" in body) {
            return (body as { documents: Array<Record<string, unknown>> }).documents;
        }
        return Array.isArray(body) ? (body as Array<Record<string, unknown>>) : [];
    }

    async listCredentials(): Promise<Array<Record<string, unknown>>> {
        return this.request("GET", "/credentials/");
    }

    async listRecordings(): Promise<Array<Record<string, unknown>>> {
        const body = await this.request<unknown>("GET", "/workflow-recordings/");
        if (body && typeof body === "object" && "recordings" in body) {
            return (body as { recordings: Array<Record<string, unknown>> }).recordings;
        }
        return Array.isArray(body) ? (body as Array<Record<string, unknown>>) : [];
    }

    // ── workflow CRUD ──────────────────────────────────────────────

    async listWorkflows(): Promise<Array<Record<string, unknown>>> {
        const body = await this.request<unknown>("GET", "/workflow/");
        if (body && typeof body === "object" && "workflows" in body) {
            return (body as { workflows: Array<Record<string, unknown>> }).workflows;
        }
        return Array.isArray(body) ? (body as Array<Record<string, unknown>>) : [];
    }

    async getWorkflow(workflowId: number): Promise<Record<string, unknown>> {
        return this.request("GET", `/workflow/${workflowId}`);
    }

    /** Fetch a workflow and return it as an editable `Workflow` object. */
    async loadWorkflow(workflowId: number): Promise<Workflow> {
        const raw = await this.getWorkflow(workflowId);
        const definition =
            (raw.current_definition as Record<string, unknown> | undefined) ??
            (raw.definition as Record<string, unknown> | undefined) ??
            {};
        const workflowJson =
            (definition.workflow_json as {
                nodes?: unknown[];
                edges?: unknown[];
            } | undefined) ??
            (raw.workflow_json as { nodes?: unknown[]; edges?: unknown[] } | undefined);
        if (!workflowJson) {
            throw new ApiError(
                200,
                `Workflow ${workflowId} has no current definition to load`,
                raw,
            );
        }
        return Workflow.fromJson(
            workflowJson as Parameters<typeof Workflow.fromJson>[0],
            { client: this, name: (raw.name as string) ?? "" },
        );
    }

    async saveWorkflow(
        workflowId: number,
        workflow: Workflow,
    ): Promise<Record<string, unknown>> {
        return this.request("PUT", `/workflow/${workflowId}`, {
            workflow_definition: workflow.toJson(),
            name: workflow.name,
        });
    }

    // ── low-level ──────────────────────────────────────────────────

    private async request<T>(
        method: string,
        path: string,
        body?: unknown,
    ): Promise<T> {
        const url = `${this.baseUrl}/api/v1${path}`;
        const init: RequestInit = {
            method,
            headers: {
                ...this.headers,
                ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
            },
            body: body !== undefined ? JSON.stringify(body) : undefined,
        };

        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeoutMs);
        init.signal = controller.signal;

        let resp: Response;
        try {
            resp = await this.fetchImpl(url, init);
        } finally {
            clearTimeout(timer);
        }

        if (!resp.ok) {
            let parsed: unknown;
            let message = resp.statusText;
            try {
                parsed = await resp.json();
                if (parsed && typeof parsed === "object") {
                    const p = parsed as Record<string, unknown>;
                    if (typeof p.detail === "string") message = p.detail;
                    else if (typeof p.message === "string") message = p.message;
                }
            } catch {
                parsed = await resp.text().catch(() => "");
                if (typeof parsed === "string" && parsed !== "") message = parsed;
            }
            throw new ApiError(resp.status, message, parsed);
        }

        if (resp.status === 204) return undefined as T;
        const text = await resp.text();
        if (text === "") return undefined as T;
        try {
            return JSON.parse(text) as T;
        } catch {
            return text as unknown as T;
        }
    }
}
