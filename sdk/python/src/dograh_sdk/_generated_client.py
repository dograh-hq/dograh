"""GENERATED — do not edit. Source: filtered OpenAPI from `api.app`.

Regenerate with `./scripts/generate_sdk.sh`.

`DograhClient` mixes in this class to get HTTP methods for every route
decorated with `sdk_expose(...)` on the backend. Request/response types
come from `_generated_models` (datamodel-codegen output).
"""

from __future__ import annotations

from typing import Any

from dograh_sdk._generated_models import (
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
)


class _GeneratedClient:
    # `DograhClient.__init__` installs `self._request` (see client.py).

    def get_node_type(self, name: str) -> NodeSpec:
        """Fetch a single node spec by name."""
        data = self._request("GET", f"/node-types/{name}")
        return NodeSpec.model_validate(data)

    def get_workflow(self, workflow_id: int) -> WorkflowResponse:
        """Get a single workflow by ID (returns draft if one exists, else published)."""
        data = self._request("GET", f"/workflow/fetch/{workflow_id}")
        return WorkflowResponse.model_validate(data)

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

    def list_workflows(self, *, status: str | None = None) -> list[WorkflowListResponse]:
        """List all workflows in the authenticated organization."""
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        data = self._request("GET", "/workflow/fetch", params=params)
        return [WorkflowListResponse.model_validate(x) for x in data]

    def test_phone_call(self, *, body: InitiateCallRequest) -> Any:
        """Place a test call from a workflow to a phone number."""
        return self._request("POST", "/telephony/initiate-call", json=body.model_dump(mode="json", exclude_none=True))

    def update_workflow(self, workflow_id: int, *, body: UpdateWorkflowRequest) -> WorkflowResponse:
        """Update a workflow's name and/or definition. Saves as a new draft."""
        data = self._request("PUT", f"/workflow/{workflow_id}", json=body.model_dump(mode="json", exclude_none=True))
        return WorkflowResponse.model_validate(data)
