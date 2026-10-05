"""GENERATED — do not edit. Source: filtered OpenAPI from `api.app`.

Regenerate with `./scripts/generate_sdk.sh`.

`DograhClient` mixes in this class to get HTTP methods for every route
decorated with `sdk_expose(...)` on the backend. Request/response types
come from `_generated_models` (datamodel-codegen output).
"""

from __future__ import annotations

from typing import Any

from dograh_sdk._generated_models import (
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
)


class _GeneratedClient:
    # `DograhClient.__init__` installs `self._request` (see client.py).

    def create_campaign(self, *, body: CreateCampaignRequest) -> CampaignResponse:
        """Create a campaign that dials a CSV source with a workflow."""
        data = self._request("POST", "/campaign/create", json=body.model_dump(mode="json", exclude_none=True))
        return CampaignResponse.model_validate(data)

    def create_folder(self, *, body: CreateFolderRequest) -> FolderResponse:
        """Create a folder in the authenticated organization."""
        data = self._request("POST", "/folder/", json=body.model_dump(mode="json", exclude_none=True))
        return FolderResponse.model_validate(data)

    def create_tool(self, *, body: CreateToolRequest) -> ToolResponse:
        """Create a reusable tool for the authenticated organization."""
        data = self._request("POST", "/tools/", json=body.model_dump(mode="json", exclude_none=True))
        return ToolResponse.model_validate(data)

    def create_workflow(self, *, body: CreateWorkflowRequest) -> WorkflowResponse:
        """Create a new workflow from a workflow definition."""
        data = self._request("POST", "/workflow/create/definition", json=body.model_dump(mode="json", exclude_none=True))
        return WorkflowResponse.model_validate(data)

    def delete_tool(self, tool_uuid: str) -> Any:
        """Archive (soft-delete) a tool by UUID."""
        return self._request("DELETE", f"/tools/{tool_uuid}")

    def get_campaign(self, campaign_id: int) -> CampaignResponse:
        """Get a campaign by ID."""
        data = self._request("GET", f"/campaign/{campaign_id}")
        return CampaignResponse.model_validate(data)

    def get_campaign_progress(self, campaign_id: int) -> CampaignProgressResponse:
        """Get live progress counts for a campaign."""
        data = self._request("GET", f"/campaign/{campaign_id}/progress")
        return CampaignProgressResponse.model_validate(data)

    def get_node_type(self, name: str) -> NodeSpec:
        """Fetch a single node spec by name."""
        data = self._request("GET", f"/node-types/{name}")
        return NodeSpec.model_validate(data)

    def get_tool(self, tool_uuid: str) -> ToolResponse:
        """Get a tool by UUID, including archived tools."""
        data = self._request("GET", f"/tools/{tool_uuid}")
        return ToolResponse.model_validate(data)

    def get_workflow(self, workflow_id: int) -> WorkflowResponse:
        """Get a single workflow by ID (returns draft if one exists, else published)."""
        data = self._request("GET", f"/workflow/fetch/{workflow_id}")
        return WorkflowResponse.model_validate(data)

    def get_workflow_run(self, workflow_id: int, run_id: int) -> WorkflowRunResponseSchema:
        """Get a single workflow run, including transcript and recording links."""
        data = self._request("GET", f"/workflow/{workflow_id}/runs/{run_id}")
        return WorkflowRunResponseSchema.model_validate(data)

    def list_campaigns(self) -> CampaignsResponse:
        """List campaigns in the authenticated organization."""
        data = self._request("GET", "/campaign/")
        return CampaignsResponse.model_validate(data)

    def list_credentials(self) -> list[CredentialResponse]:
        """List webhook credentials available to the authenticated organization."""
        data = self._request("GET", "/credentials/")
        return [CredentialResponse.model_validate(x) for x in data]

    def list_documents(self, *, status: str | None = None, limit: int | None = None, offset: int | None = None) -> DocumentListResponseSchema:
        """List knowledge base documents available to the authenticated organization."""
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        data = self._request("GET", "/knowledge-base/documents", params=params)
        return DocumentListResponseSchema.model_validate(data)

    def list_folders(self) -> list[FolderResponse]:
        """List folders in the authenticated organization."""
        data = self._request("GET", "/folder/")
        return [FolderResponse.model_validate(x) for x in data]

    def list_node_types(self) -> NodeTypesResponse:
        """List every registered node type with its spec. Pinned to spec_version."""
        data = self._request("GET", "/node-types")
        return NodeTypesResponse.model_validate(data)

    def list_recordings(self, *, workflow_id: int | None = None, tts_provider: str | None = None, tts_model: str | None = None, tts_voice_id: str | None = None) -> RecordingListResponseSchema:
        """List workflow recordings available to the authenticated organization."""
        params: dict[str, Any] = {}
        if workflow_id is not None:
            params["workflow_id"] = workflow_id
        if tts_provider is not None:
            params["tts_provider"] = tts_provider
        if tts_model is not None:
            params["tts_model"] = tts_model
        if tts_voice_id is not None:
            params["tts_voice_id"] = tts_voice_id
        data = self._request("GET", "/workflow-recordings/", params=params)
        return RecordingListResponseSchema.model_validate(data)

    def list_tools(self, *, status: str | None = None, category: str | None = None) -> list[ToolResponse]:
        """List tools available to the authenticated organization."""
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        if category is not None:
            params["category"] = category
        data = self._request("GET", "/tools/", params=params)
        return [ToolResponse.model_validate(x) for x in data]

    def list_workflow_runs(self, workflow_id: int, *, page: int | None = None, limit: int | None = None, filters: str | None = None, sort_by: str | None = None, sort_order: str | None = None) -> WorkflowRunsResponse:
        """List workflow runs for a workflow in the authenticated organization."""
        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if limit is not None:
            params["limit"] = limit
        if filters is not None:
            params["filters"] = filters
        if sort_by is not None:
            params["sort_by"] = sort_by
        if sort_order is not None:
            params["sort_order"] = sort_order
        data = self._request("GET", f"/workflow/{workflow_id}/runs", params=params)
        return WorkflowRunsResponse.model_validate(data)

    def list_workflows(self, *, status: str | None = None) -> list[WorkflowListResponse]:
        """List all workflows in the authenticated organization."""
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        data = self._request("GET", "/workflow/fetch", params=params)
        return [WorkflowListResponse.model_validate(x) for x in data]

    def pause_campaign(self, campaign_id: int) -> CampaignResponse:
        """Pause a running campaign."""
        data = self._request("POST", f"/campaign/{campaign_id}/pause")
        return CampaignResponse.model_validate(data)

    def publish_workflow(self, workflow_id: int) -> WorkflowVersionSummaryResponse:
        """Publish the current draft of a workflow after validation."""
        data = self._request("POST", f"/workflow/{workflow_id}/publish")
        return WorkflowVersionSummaryResponse.model_validate(data)

    def resume_campaign(self, campaign_id: int) -> CampaignResponse:
        """Resume a paused campaign."""
        data = self._request("POST", f"/campaign/{campaign_id}/resume")
        return CampaignResponse.model_validate(data)

    def start_campaign(self, campaign_id: int) -> CampaignResponse:
        """Start a campaign that is ready to dial."""
        data = self._request("POST", f"/campaign/{campaign_id}/start")
        return CampaignResponse.model_validate(data)

    def test_phone_call(self, *, body: InitiateCallRequest) -> Any:
        """Place a test call from a workflow to a phone number."""
        return self._request("POST", "/telephony/initiate-call", json=body.model_dump(mode="json", exclude_none=True))

    def update_tool(self, tool_uuid: str, *, body: UpdateToolRequest) -> ToolResponse:
        """Update a tool's name, description, definition, or status."""
        data = self._request("PUT", f"/tools/{tool_uuid}", json=body.model_dump(mode="json", exclude_none=True))
        return ToolResponse.model_validate(data)

    def update_workflow(self, workflow_id: int, *, body: UpdateWorkflowRequest) -> WorkflowResponse:
        """Update a workflow's name and/or definition. Saves as a new draft."""
        data = self._request("PUT", f"/workflow/{workflow_id}", json=body.model_dump(mode="json", exclude_none=True))
        return WorkflowResponse.model_validate(data)

    def validate_workflow(self, workflow_id: int) -> ValidateWorkflowResponse:
        """Validate a workflow draft, or the published definition when no draft exists."""
        data = self._request("POST", f"/workflow/{workflow_id}/validate")
        return ValidateWorkflowResponse.model_validate(data)
