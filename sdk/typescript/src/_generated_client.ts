// GENERATED — do not edit. Source: filtered OpenAPI from `api.app`.
//
// Regenerate with `./scripts/generate_sdk.sh`.
//
// `DograhClient` extends this base to get HTTP methods for every route
// decorated with `sdk_expose(...)`. Request/response types come from
// `_generated_models` (openapi-typescript output, --root-types).

import type {
    CreateWorkflowRequest,
    CredentialResponse,
    DocumentListResponseSchema,
    InitiateCallRequest,
    NodeSpec,
    NodeTypesResponse,
    RecordingListResponseSchema,
    ToolResponse,
    UpdateWorkflowRequest,
    WorkflowListResponse,
    WorkflowResponse,
} from "./_generated_models.js";

export abstract class _GeneratedClient {
    protected abstract request<T = unknown>(
        method: string,
        path: string,
        opts?: { json?: unknown; params?: Record<string, unknown> },
    ): Promise<T>;

    /** Create a new workflow from a workflow definition. */
    async createWorkflow(opts: { body: CreateWorkflowRequest }): Promise<WorkflowResponse> {
        return this.request<WorkflowResponse>("POST", "/workflow/create/definition", { json: opts.body });
    }

    /** Fetch a single node spec by name. */
    async getNodeType(name: string): Promise<NodeSpec> {
        return this.request<NodeSpec>("GET", `/node-types/${name}`);
    }

    /** Get a single workflow by ID (returns draft if one exists, else published). */
    async getWorkflow(workflowId: number): Promise<WorkflowResponse> {
        return this.request<WorkflowResponse>("GET", `/workflow/fetch/${workflowId}`);
    }

    /** List webhook credentials available to the authenticated organization. */
    async listCredentials(): Promise<CredentialResponse[]> {
        return this.request<CredentialResponse[]>("GET", "/credentials/");
    }

    /** List knowledge base documents available to the authenticated organization. */
    async listDocuments(opts: { status?: string; limit?: number; offset?: number } = {}): Promise<DocumentListResponseSchema> {
        const params: Record<string, unknown> = {
            ...(opts.status !== undefined ? { "status": opts.status } : {}),
            ...(opts.limit !== undefined ? { "limit": opts.limit } : {}),
            ...(opts.offset !== undefined ? { "offset": opts.offset } : {}),
        };
        return this.request<DocumentListResponseSchema>("GET", "/knowledge-base/documents", { params });
    }

    /** List every registered node type with its spec. Pinned to spec_version. */
    async listNodeTypes(): Promise<NodeTypesResponse> {
        return this.request<NodeTypesResponse>("GET", "/node-types");
    }

    /** List workflow recordings available to the authenticated organization. */
    async listRecordings(opts: { workflowId?: number; ttsProvider?: string; ttsModel?: string; ttsVoiceId?: string } = {}): Promise<RecordingListResponseSchema> {
        const params: Record<string, unknown> = {
            ...(opts.workflowId !== undefined ? { "workflow_id": opts.workflowId } : {}),
            ...(opts.ttsProvider !== undefined ? { "tts_provider": opts.ttsProvider } : {}),
            ...(opts.ttsModel !== undefined ? { "tts_model": opts.ttsModel } : {}),
            ...(opts.ttsVoiceId !== undefined ? { "tts_voice_id": opts.ttsVoiceId } : {}),
        };
        return this.request<RecordingListResponseSchema>("GET", "/workflow-recordings/", { params });
    }

    /** List tools available to the authenticated organization. */
    async listTools(opts: { status?: string; category?: string } = {}): Promise<ToolResponse[]> {
        const params: Record<string, unknown> = {
            ...(opts.status !== undefined ? { "status": opts.status } : {}),
            ...(opts.category !== undefined ? { "category": opts.category } : {}),
        };
        return this.request<ToolResponse[]>("GET", "/tools/", { params });
    }

    /** List all workflows in the authenticated organization. */
    async listWorkflows(opts: { status?: string } = {}): Promise<WorkflowListResponse[]> {
        const params: Record<string, unknown> = {
            ...(opts.status !== undefined ? { "status": opts.status } : {}),
        };
        return this.request<WorkflowListResponse[]>("GET", "/workflow/fetch", { params });
    }

    /** Place a test call from a workflow to a phone number. */
    async testPhoneCall(opts: { body: InitiateCallRequest }): Promise<unknown> {
        return this.request("POST", "/telephony/initiate-call", { json: opts.body });
    }

    /** Update a workflow's name and/or definition. Saves as a new draft. */
    async updateWorkflow(workflowId: number, opts: { body: UpdateWorkflowRequest }): Promise<WorkflowResponse> {
        return this.request<WorkflowResponse>("PUT", `/workflow/${workflowId}`, { json: opts.body });
    }
}
