// HTTP client for the Dograh REST API.
//
// Most endpoint methods come from `_GeneratedClient` (auto-generated from
// the FastAPI OpenAPI spec — see `scripts/generate_sdk.sh`). This class
// adds session/auth/caching around that base plus the ergonomic
// `loadWorkflow` / `saveWorkflow` wrappers that compose a generated call
// with local `Workflow` hydration.

import { _GeneratedClient } from "./_generated_client.js";
import type {
    NodeSpec,
    NodeTypesResponse,
    UpdateWorkflowRequest,
    WorkflowResponse,
} from "./_generated_models.js";
import { ApiError, SpecMismatchError } from "./errors.js";
import { Workflow, type SpecProvider } from "./workflow.js";

export interface DograhClientOptions {
    baseUrl?: string;
    apiKey?: string;
    /** Request timeout in ms. */
    timeoutMs?: number;
    /** Optional fetch override for tests / custom transports. */
    fetch?: typeof globalThis.fetch;
}

export class DograhClient extends _GeneratedClient implements SpecProvider {
    readonly baseUrl: string;
    readonly apiKey: string | undefined;
    private readonly fetchImpl: typeof globalThis.fetch;
    private readonly timeoutMs: number;
    private readonly headers: Record<string, string>;
    private readonly specCache = new Map<string, NodeSpec>();
    private specVersionCache: string | null = null;

    constructor(opts: DograhClientOptions = {}) {
        super();
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

    /** Spec contract version reported by the server, or null until the
     * first `listNodeTypes` / `getNodeType` call. */
    get specVersion(): string | null {
        return this.specVersionCache;
    }

    // ── spec discovery overrides (generated methods + caching) ────────

    async listNodeTypes(): Promise<NodeTypesResponse> {
        const resp = await super.listNodeTypes();
        this.specVersionCache = resp.spec_version;
        for (const spec of resp.node_types ?? []) {
            this.specCache.set(spec.name, spec);
        }
        return resp;
    }

    async getNodeType(name: string): Promise<NodeSpec> {
        const cached = this.specCache.get(name);
        if (cached) return cached;
        try {
            const spec = await super.getNodeType(name);
            this.specCache.set(name, spec);
            return spec;
        } catch (err) {
            if (err instanceof ApiError && err.statusCode === 404) {
                throw new SpecMismatchError(`Unknown node type: ${JSON.stringify(name)}`);
            }
            throw err;
        }
    }

    // ── ergonomic workflow wrappers ───────────────────────────────────

    /** Fetch a workflow and return it as an editable `Workflow` builder. */
    async loadWorkflow(workflowId: number): Promise<Workflow> {
        const resp = await this.getWorkflow(workflowId);
        if (!resp.workflow_definition) {
            throw new ApiError(
                200,
                `Workflow ${workflowId} has no definition to load`,
                resp,
            );
        }
        return Workflow.fromJson(
            resp.workflow_definition as Parameters<typeof Workflow.fromJson>[0],
            { client: this, name: resp.name ?? "" },
        );
    }

    async saveWorkflow(workflowId: number, workflow: Workflow): Promise<WorkflowResponse> {
        const body: UpdateWorkflowRequest = {
            name: workflow.name,
            workflow_definition: workflow.toJson() as unknown as Record<string, unknown>,
        };
        return this.updateWorkflow(workflowId, { body });
    }

    // ── low-level (overrides `_GeneratedClient.request`) ──────────────

    protected async request<T = unknown>(
        method: string,
        path: string,
        opts?: { json?: unknown; params?: Record<string, unknown> },
    ): Promise<T> {
        let url = `${this.baseUrl}/api/v1${path}`;
        if (opts?.params) {
            const qs = new URLSearchParams();
            for (const [k, v] of Object.entries(opts.params)) {
                if (v !== undefined && v !== null) qs.append(k, String(v));
            }
            const q = qs.toString();
            if (q) url += (url.includes("?") ? "&" : "?") + q;
        }

        const hasBody = opts?.json !== undefined;
        const init: RequestInit = {
            method,
            headers: {
                ...this.headers,
                ...(hasBody ? { "Content-Type": "application/json" } : {}),
            },
            body: hasBody ? JSON.stringify(opts!.json) : undefined,
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
