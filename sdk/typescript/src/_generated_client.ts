// GENERATED — do not edit. Source: filtered OpenAPI from `api.app`.
//
// Regenerate with `./scripts/generate_sdk.sh`.
//
// `DograhClient` extends this base to get HTTP methods for every route
// decorated with `sdk_expose(...)`. Request/response types come from
// `_generated_models` (openapi-typescript output, --root-types).

import type {
    CampaignProgressResponse,
    CampaignResponse,
    CampaignsResponse,
    CreateCampaignRequest,
    CreateFolderRequest,
    CreateToolRequest,
    CreateWorkflowRequest,
    CredentialResponse,
    DocumentListResponseSchema,
    FolderResponse,
    InitiateCallRequest,
    NodeSpec,
    NodeTypesResponse,
    RecordingListResponseSchema,
    ToolResponse,
    UpdateToolRequest,
    UpdateWorkflowRequest,
    ValidateWorkflowResponse,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowRunResponseSchema,
    WorkflowRunsResponse,
    WorkflowVersionSummaryResponse,
} from "./_generated_models.js";

export abstract class _GeneratedClient {
    protected abstract request<T = unknown>(
        method: string,
        path: string,
        opts?: { json?: unknown; params?: Record<string, unknown> },
    ): Promise<T>;

    /** Create a campaign that dials a CSV source with a workflow. */
    async createCampaign(opts: { body: CreateCampaignRequest }): Promise<CampaignResponse> {
        return this.request<CampaignResponse>("POST", "/campaign/create", { json: opts.body });
    }

    /** Create a folder in the authenticated organization. */
    async createFolder(opts: { body: CreateFolderRequest }): Promise<FolderResponse> {
        return this.request<FolderResponse>("POST", "/folder/", { json: opts.body });
    }

    /** Create a reusable tool for the authenticated organization. */
    async createTool(opts: { body: CreateToolRequest }): Promise<ToolResponse> {
        return this.request<ToolResponse>("POST", "/tools/", { json: opts.body });
    }

    /** Create a new workflow from a workflow definition. */
    async createWorkflow(opts: { body: CreateWorkflowRequest }): Promise<WorkflowResponse> {
        return this.request<WorkflowResponse>("POST", "/workflow/create/definition", { json: opts.body });
    }

    /** Archive (soft-delete) a tool by UUID. */
    async deleteTool(toolUuid: string): Promise<unknown> {
        return this.request("DELETE", `/tools/${toolUuid}`);
    }

    /** Get a campaign by ID. */
    async getCampaign(campaignId: number): Promise<CampaignResponse> {
        return this.request<CampaignResponse>("GET", `/campaign/${campaignId}`);
    }

    /** Get live progress counts for a campaign. */
    async getCampaignProgress(campaignId: number): Promise<CampaignProgressResponse> {
        return this.request<CampaignProgressResponse>("GET", `/campaign/${campaignId}/progress`);
    }

    /** Fetch a single node spec by name. */
    async getNodeType(name: string): Promise<NodeSpec> {
        return this.request<NodeSpec>("GET", `/node-types/${name}`);
    }

    /** Get a tool by UUID, including archived tools. */
    async getTool(toolUuid: string): Promise<ToolResponse> {
        return this.request<ToolResponse>("GET", `/tools/${toolUuid}`);
    }

    /** Get a single workflow by ID (returns draft if one exists, else published). */
    async getWorkflow(workflowId: number): Promise<WorkflowResponse> {
        return this.request<WorkflowResponse>("GET", `/workflow/fetch/${workflowId}`);
    }

    /** Get a single workflow run, including transcript and recording links. */
    async getWorkflowRun(workflowId: number, runId: number): Promise<WorkflowRunResponseSchema> {
        return this.request<WorkflowRunResponseSchema>("GET", `/workflow/${workflowId}/runs/${runId}`);
    }

    /** List campaigns in the authenticated organization. */
    async listCampaigns(): Promise<CampaignsResponse> {
        return this.request<CampaignsResponse>("GET", "/campaign/");
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

    /** List folders in the authenticated organization. */
    async listFolders(): Promise<FolderResponse[]> {
        return this.request<FolderResponse[]>("GET", "/folder/");
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

    /** List workflow runs for a workflow in the authenticated organization. */
    async listWorkflowRuns(workflowId: number, opts: { page?: number; limit?: number; filters?: string; sortBy?: string; sortOrder?: string } = {}): Promise<WorkflowRunsResponse> {
        const params: Record<string, unknown> = {
            ...(opts.page !== undefined ? { "page": opts.page } : {}),
            ...(opts.limit !== undefined ? { "limit": opts.limit } : {}),
            ...(opts.filters !== undefined ? { "filters": opts.filters } : {}),
            ...(opts.sortBy !== undefined ? { "sort_by": opts.sortBy } : {}),
            ...(opts.sortOrder !== undefined ? { "sort_order": opts.sortOrder } : {}),
        };
        return this.request<WorkflowRunsResponse>("GET", `/workflow/${workflowId}/runs`, { params });
    }

    /** List all workflows in the authenticated organization. */
    async listWorkflows(opts: { status?: string } = {}): Promise<WorkflowListResponse[]> {
        const params: Record<string, unknown> = {
            ...(opts.status !== undefined ? { "status": opts.status } : {}),
        };
        return this.request<WorkflowListResponse[]>("GET", "/workflow/fetch", { params });
    }

    /** Pause a running campaign. */
    async pauseCampaign(campaignId: number): Promise<CampaignResponse> {
        return this.request<CampaignResponse>("POST", `/campaign/${campaignId}/pause`);
    }

    /** Publish the current draft of a workflow after validation. */
    async publishWorkflow(workflowId: number): Promise<WorkflowVersionSummaryResponse> {
        return this.request<WorkflowVersionSummaryResponse>("POST", `/workflow/${workflowId}/publish`);
    }

    /** Resume a paused campaign. */
    async resumeCampaign(campaignId: number): Promise<CampaignResponse> {
        return this.request<CampaignResponse>("POST", `/campaign/${campaignId}/resume`);
    }

    /** Start a campaign that is ready to dial. */
    async startCampaign(campaignId: number): Promise<CampaignResponse> {
        return this.request<CampaignResponse>("POST", `/campaign/${campaignId}/start`);
    }

    /** Place a test call from a workflow to a phone number. */
    async testPhoneCall(opts: { body: InitiateCallRequest }): Promise<unknown> {
        return this.request("POST", "/telephony/initiate-call", { json: opts.body });
    }

    /** Update a tool's name, description, icon, icon color, definition, or status. */
    async updateTool(toolUuid: string, opts: { body: UpdateToolRequest }): Promise<ToolResponse> {
        return this.request<ToolResponse>("PUT", `/tools/${toolUuid}`, { json: opts.body });
    }

    /** Update a workflow's name and/or definition. Saves as a new draft. */
    async updateWorkflow(workflowId: number, opts: { body: UpdateWorkflowRequest }): Promise<WorkflowResponse> {
        return this.request<WorkflowResponse>("PUT", `/workflow/${workflowId}`, { json: opts.body });
    }

    /** Validate a workflow draft, or the published definition when no draft exists. */
    async validateWorkflow(workflowId: number): Promise<ValidateWorkflowResponse> {
        return this.request<ValidateWorkflowResponse>("POST", `/workflow/${workflowId}/validate`);
    }
}
