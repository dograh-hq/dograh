import json
import re
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from httpx import HTTPStatusError
from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.db.agent_trigger_client import TriggerPathConflictError
from api.db.models import UserModel
from api.db.workflow_template_client import WorkflowTemplateClient
from api.enums import CallType, PostHogEvent, StorageBackend
from api.schemas.workflow import WorkflowRunResponseSchema
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.campaign.report import generate_workflow_report_csv
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.masking import (
    mask_workflow_definition,
    merge_workflow_api_keys,
)
from api.services.configuration.resolve import resolve_effective_config
from api.services.mps_service_key_client import mps_service_key_client
from api.services.posthog_client import capture_event
from api.services.storage import storage_fs
from api.services.workflow.dto import ReactFlowDTO, sanitize_workflow_definition
from api.services.workflow.duplicate import duplicate_workflow
from api.services.workflow.errors import ItemKind, WorkflowError
from api.services.workflow.workflow import WorkflowGraph


def extract_trigger_paths(workflow_definition: dict) -> List[str]:
    """Extract trigger UUIDs from workflow definition.

    Args:
        workflow_definition: The workflow definition JSON

    Returns:
        List of trigger UUIDs found in the workflow
    """
    if not workflow_definition:
        return []

    nodes = workflow_definition.get("nodes", [])
    trigger_paths = []

    for node in nodes:
        if node.get("type") == "trigger":
            trigger_path = node.get("data", {}).get("trigger_path")
            if trigger_path:
                trigger_paths.append(trigger_path)

    return trigger_paths


def _trigger_path_to_node_id(workflow_definition: dict) -> dict[str, str]:
    """Map each trigger node's trigger_path to its node id."""
    if not workflow_definition:
        return {}
    out: dict[str, str] = {}
    for node in workflow_definition.get("nodes", []):
        if node.get("type") == "trigger":
            tp = node.get("data", {}).get("trigger_path")
            if tp:
                out[tp] = node.get("id")
    return out


def regenerate_trigger_uuids(workflow_definition: dict) -> dict:
    """Regenerate UUIDs for all trigger nodes in a workflow definition.

    This should be called when creating a new workflow from a template or
    duplicating a workflow to avoid trigger UUID conflicts.

    Args:
        workflow_definition: The workflow definition JSON

    Returns:
        Updated workflow definition with new trigger UUIDs
    """
    if not workflow_definition:
        return workflow_definition

    # Deep copy to avoid modifying the original
    import copy

    updated_definition = copy.deepcopy(workflow_definition)

    nodes = updated_definition.get("nodes", [])
    for node in nodes:
        if node.get("type") == "trigger":
            # Generate a new UUID for this trigger
            if "data" not in node:
                node["data"] = {}
            node["data"]["trigger_path"] = str(uuid.uuid4())

    return updated_definition


def ensure_trigger_paths(workflow_definition: Optional[dict]) -> Optional[dict]:
    """Mint a UUID for any trigger node that's missing ``data.trigger_path``.

    Trigger nodes that already carry a non-empty trigger_path are left
    untouched so stable IDs survive edits. The input is not mutated; the
    returned dict is what should be persisted and echoed in the response.
    """
    if not workflow_definition:
        return workflow_definition

    import copy

    out = copy.deepcopy(workflow_definition)
    for node in out.get("nodes") or []:
        if node.get("type") != "trigger":
            continue
        data = node.setdefault("data", {})
        if not data.get("trigger_path"):
            data["trigger_path"] = str(uuid.uuid4())
    return out


router = APIRouter(prefix="/workflow")


class ValidateWorkflowResponse(BaseModel):
    is_valid: bool
    errors: list[WorkflowError]


def _trigger_conflict_http_exception(
    workflow_definition: Optional[dict], conflicting_paths: list[str]
) -> HTTPException:
    """Build a 409 with the same detail shape as validate's 422 so the editor
    can highlight the offending trigger node(s) using the same code path."""
    path_to_node = (
        _trigger_path_to_node_id(workflow_definition) if workflow_definition else {}
    )
    errors: list[WorkflowError] = [
        WorkflowError(
            kind=ItemKind.node,
            id=path_to_node.get(p),
            field="data.trigger_path",
            message=(
                "Trigger path is already in use. Please choose another one "
                "or leave empty to use a unique one generated by the server."
            ),
        )
        for p in conflicting_paths
    ]
    return HTTPException(
        status_code=409,
        detail=ValidateWorkflowResponse(is_valid=False, errors=errors).model_dump(),
    )


async def _validate_workflow_definition(
    workflow_definition: Optional[dict],
    exclude_workflow_id: Optional[int] = None,
) -> list[WorkflowError]:
    """Run DTO + graph + trigger-conflict checks on a workflow definition.

    Returns the list of errors (empty if the definition is valid). This is
    the single source of truth for "is this workflow valid?" — used by the
    /validate route (read-only audit) and the /publish route (gate).
    """
    errors: list[WorkflowError] = []
    if not workflow_definition:
        return errors

    # ----------- DTO Validation ------------
    dto: Optional[ReactFlowDTO] = None
    try:
        dto = ReactFlowDTO.model_validate(workflow_definition)
    except ValidationError as exc:
        errors.extend(_transform_schema_errors(exc, workflow_definition))

    # ----------- Graph Validation if DTO is valid ------------
    try:
        if dto:
            WorkflowGraph(dto)
    except ValueError as e:
        errors.extend(e.args[0])

    # ----------- Trigger Path Conflict Check ------------
    trigger_paths = extract_trigger_paths(workflow_definition)
    if trigger_paths:
        conflicts = await db_client.check_trigger_path_conflicts(
            trigger_paths=trigger_paths,
            exclude_workflow_id=exclude_workflow_id,
        )
        if conflicts:
            path_to_node = _trigger_path_to_node_id(workflow_definition)
            for conflicting_path in conflicts:
                errors.append(
                    WorkflowError(
                        kind=ItemKind.node,
                        id=path_to_node.get(conflicting_path),
                        field="data.trigger_path",
                        message=(
                            "Trigger path is already in use. Please choose "
                            "another one or leave empty to use a unique one "
                            "generated by the server."
                        ),
                    )
                )

    return errors


def _validation_errors_http_exception(
    errors: list[WorkflowError], status_code: int = 422
) -> HTTPException:
    """Wrap a list of validation errors in the response shape clients expect."""
    return HTTPException(
        status_code=status_code,
        detail=ValidateWorkflowResponse(is_valid=False, errors=errors).model_dump(),
    )


class CallDispositionCodes(BaseModel):
    disposition_codes: list[str] = []


class WorkflowResponse(BaseModel):
    id: int
    name: str
    status: str
    created_at: datetime
    workflow_definition: dict
    current_definition_id: int | None
    template_context_variables: dict | None = None
    call_disposition_codes: CallDispositionCodes | None = None
    total_runs: int | None = None
    workflow_configurations: dict | None = None
    version_number: int | None = None
    version_status: str | None = None
    workflow_uuid: str | None = None


class WorkflowListResponse(BaseModel):
    """Lightweight response for workflow listings (excludes large fields)."""

    id: int
    name: str
    status: str
    created_at: datetime
    total_runs: int


class WorkflowCountResponse(BaseModel):
    """Response for workflow count endpoint."""

    total: int
    active: int
    archived: int


class WorkflowTemplateResponse(BaseModel):
    id: int
    template_name: str
    template_description: str
    template_json: dict
    created_at: datetime


class CreateWorkflowRequest(BaseModel):
    name: str
    workflow_definition: dict


class DuplicateTemplateRequest(BaseModel):
    template_id: int
    workflow_name: str


class UpdateWorkflowRequest(BaseModel):
    name: str | None = None
    workflow_definition: dict | None = None
    template_context_variables: dict | None = None
    workflow_configurations: dict | None = None


class WorkflowVersionResponse(BaseModel):
    id: int
    version_number: int
    status: str
    created_at: datetime
    published_at: datetime | None = None
    workflow_json: dict
    workflow_configurations: dict | None = None
    template_context_variables: dict | None = None


class UpdateWorkflowStatusRequest(BaseModel):
    status: str  # "active" or "archived"


class CreateWorkflowRunRequest(BaseModel):
    mode: str
    name: str


class CreateWorkflowRunResponse(BaseModel):
    id: int
    workflow_id: int
    name: str
    mode: str
    created_at: datetime
    definition_id: int
    initial_context: dict | None = None


class CreateWorkflowTemplateRequest(BaseModel):
    call_type: Literal[CallType.INBOUND.value, CallType.OUTBOUND.value]
    use_case: str
    activity_description: str


@router.post("/{workflow_id}/validate")
async def validate_workflow(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> ValidateWorkflowResponse:
    """
    Validate all nodes in a workflow to ensure they have required fields.

    Args:
        workflow_id: The ID of the workflow to validate
        user: The authenticated user

    Returns:
        Object indicating if workflow is valid and any invalid nodes/edges
    """
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )

    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Validate draft if it exists (user is editing), else validate published
    draft = await db_client.get_draft_version(workflow_id)
    workflow_definition = (
        draft.workflow_json if draft else workflow.released_definition.workflow_json
    )

    errors = await _validate_workflow_definition(
        workflow_definition, exclude_workflow_id=workflow_id
    )

    if errors:
        raise _validation_errors_http_exception(errors)

    return ValidateWorkflowResponse(is_valid=True, errors=[])


def _transform_schema_errors(
    exc: ValidationError, workflow_definition: dict
) -> list[WorkflowError]:
    out: list[WorkflowError] = []

    for err in exc.errors():
        loc = err["loc"]
        idx = workflow_definition[loc[0]][loc[1]]["id"]

        kind: ItemKind = ItemKind.node if loc[0] == "nodes" else ItemKind.edge

        out.append(
            WorkflowError(
                kind=kind,
                id=idx,
                field=".".join(str(p) for p in err["loc"][2:]) or None,
                message=err["msg"].capitalize(),
            )
        )
    return out


@router.post(
    "/create/definition",
    **sdk_expose(
        method="create_workflow",
        description="Create a new workflow from a workflow definition.",
    ),
)
async def create_workflow(
    request: CreateWorkflowRequest, user: UserModel = Depends(get_user)
) -> WorkflowResponse:
    """
    Create a new workflow from the client

    Args:
        request: The create workflow request
        user: The user to create the workflow for
    """
    # Auto-mint trigger_path for any trigger node that didn't ship one so
    # clients don't need to generate UUIDs themselves.
    workflow_definition = ensure_trigger_paths(request.workflow_definition)

    # Validate trigger path uniqueness BEFORE creating the workflow so we
    # don't leave an orphaned workflow record when the trigger conflicts.
    trigger_paths = (
        extract_trigger_paths(workflow_definition) if workflow_definition else []
    )
    if trigger_paths:
        try:
            await db_client.assert_trigger_paths_available(
                trigger_paths=trigger_paths,
            )
        except TriggerPathConflictError as e:
            raise _trigger_conflict_http_exception(workflow_definition, e.trigger_paths)

    workflow = await db_client.create_workflow(
        request.name,
        workflow_definition,
        user.id,
        user.selected_organization_id,
    )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.WORKFLOW_CREATED,
        properties={
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "source": "direct",
            "organization_id": user.selected_organization_id,
        },
    )

    if trigger_paths:
        await db_client.sync_triggers_for_workflow(
            workflow_id=workflow.id,
            organization_id=user.selected_organization_id,
            trigger_paths=trigger_paths,
        )

    return {
        "id": workflow.id,
        "name": workflow.name,
        "status": workflow.status,
        "created_at": workflow.created_at,
        "workflow_definition": mask_workflow_definition(workflow_definition),
        "current_definition_id": workflow.current_definition_id,
        "template_context_variables": workflow.template_context_variables,
        "call_disposition_codes": workflow.call_disposition_codes,
        "workflow_configurations": workflow.workflow_configurations,
    }


@router.post("/create/template")
async def create_workflow_from_template(
    request: CreateWorkflowTemplateRequest,
    user: UserModel = Depends(get_user),
) -> WorkflowResponse:
    """
    Create a new workflow from a natural language template request.

    This endpoint:
    1. Uses mps_service_key_client to call MPS workflow API
    2. Passes organization ID (authenticated mode) or created_by (OSS mode)
    3. Creates the workflow in the database

    Args:
        request: The template creation request with call_type, use_case, and activity_description
        user: The authenticated user

    Returns:
        The created workflow

    Raises:
        HTTPException: If MPS API call fails
    """
    try:
        # Call MPS API to generate workflow using the client
        if DEPLOYMENT_MODE == "oss":
            workflow_data = await mps_service_key_client.call_workflow_api(
                call_type=request.call_type.upper(),
                use_case=request.use_case,
                activity_description=request.activity_description,
                created_by=str(user.provider_id),
            )
        else:
            if not user.selected_organization_id:
                raise HTTPException(status_code=400, detail="No organization selected")

            workflow_data = await mps_service_key_client.call_workflow_api(
                call_type=request.call_type.upper(),
                use_case=request.use_case,
                activity_description=request.activity_description,
                organization_id=user.selected_organization_id,
            )

        # Create the workflow in our database
        # Regenerate trigger UUIDs to avoid conflicts with existing triggers
        workflow_def = regenerate_trigger_uuids(
            workflow_data.get("workflow_definition", {})
        )

        trigger_paths = extract_trigger_paths(workflow_def) if workflow_def else []
        if trigger_paths:
            try:
                await db_client.assert_trigger_paths_available(
                    trigger_paths=trigger_paths,
                )
            except TriggerPathConflictError as e:
                raise HTTPException(status_code=409, detail=str(e))

        workflow = await db_client.create_workflow(
            name=workflow_data.get("name", f"{request.use_case} - {request.call_type}"),
            workflow_definition=workflow_def,
            user_id=user.id,
            organization_id=user.selected_organization_id,
        )

        capture_event(
            distinct_id=str(user.provider_id),
            event=PostHogEvent.WORKFLOW_CREATED,
            properties={
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "source": "template",
                "call_type": request.call_type,
                "use_case": request.use_case,
                "organization_id": user.selected_organization_id,
            },
        )

        if trigger_paths:
            await db_client.sync_triggers_for_workflow(
                workflow_id=workflow.id,
                organization_id=user.selected_organization_id,
                trigger_paths=trigger_paths,
            )

        return {
            "id": workflow.id,
            "name": workflow.name,
            "status": workflow.status,
            "created_at": workflow.created_at,
            "workflow_definition": mask_workflow_definition(workflow_def),
            "current_definition_id": workflow.current_definition_id,
            "template_context_variables": workflow.template_context_variables,
            "call_disposition_codes": workflow.call_disposition_codes,
            "workflow_configurations": workflow.workflow_configurations,
        }

    except HTTPException:
        raise
    except HTTPStatusError as e:
        logger.error(f"MPS API error: {e}")
        raise HTTPException(
            status_code=e.response.status_code if hasattr(e, "response") else 500,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error creating workflow from template: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}",
        )


class WorkflowSummaryResponse(BaseModel):
    id: int
    name: str


@router.get("/count")
async def get_workflow_count(
    user: UserModel = Depends(get_user),
) -> WorkflowCountResponse:
    """Get workflow counts for the authenticated user's organization.

    This is a lightweight endpoint for checking if the user has workflows,
    useful for redirect logic without fetching full workflow data.
    """
    counts = await db_client.get_workflow_counts(
        organization_id=user.selected_organization_id
    )

    return WorkflowCountResponse(
        total=counts["total"],
        active=counts["active"],
        archived=counts["archived"],
    )


@router.get(
    "/fetch",
    **sdk_expose(
        method="list_workflows",
        description="List all workflows in the authenticated organization.",
    ),
)
async def get_workflows(
    user: UserModel = Depends(get_user),
    status: Optional[str] = Query(
        None,
        description="Filter by status - can be single value (active/archived) or comma-separated (active,archived)",
    ),
) -> List[WorkflowListResponse]:
    """Get all workflows for the authenticated user's organization.

    Returns a lightweight response with only essential fields for listing.
    Use GET /workflow/fetch/{workflow_id} to get full workflow details.
    """
    # Handle comma-separated status values
    if status and "," in status:
        # Split comma-separated values and fetch workflows for each status
        status_list = [s.strip() for s in status.split(",")]
        all_workflows = []
        for status_value in status_list:
            workflows = await db_client.get_all_workflows_for_listing(
                organization_id=user.selected_organization_id, status=status_value
            )
            all_workflows.extend(workflows)
        workflows = all_workflows
    else:
        # Single status or no status filter
        workflows = await db_client.get_all_workflows_for_listing(
            organization_id=user.selected_organization_id, status=status
        )

    # Get run counts for all workflows in a single query
    workflow_ids = [workflow.id for workflow in workflows]
    run_counts = await db_client.get_workflow_run_counts(workflow_ids)

    return [
        WorkflowListResponse(
            id=workflow.id,
            name=workflow.name,
            status=workflow.status,
            created_at=workflow.created_at,
            total_runs=run_counts.get(workflow.id, 0),
        )
        for workflow in workflows
    ]


@router.get(
    "/fetch/{workflow_id}",
    **sdk_expose(
        method="get_workflow",
        description="Get a single workflow by ID (returns draft if one exists, else published).",
    ),
)
async def get_workflow(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> WorkflowResponse:
    """Get a single workflow by ID.

    If a draft version exists, returns the draft content for editing.
    Otherwise returns the published version's content.
    """
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Check for draft — editor should show draft content if it exists
    draft = await db_client.get_draft_version(workflow_id)

    if draft:
        workflow_def = draft.workflow_json
        workflow_configs = draft.workflow_configurations
        template_vars = draft.template_context_variables
    else:
        published = workflow.released_definition
        workflow_def = published.workflow_json
        workflow_configs = published.workflow_configurations
        template_vars = published.template_context_variables

    active_def = draft or workflow.released_definition
    return {
        "id": workflow.id,
        "name": workflow.name,
        "status": workflow.status,
        "created_at": workflow.created_at,
        "workflow_definition": mask_workflow_definition(workflow_def),
        "current_definition_id": workflow.current_definition_id,
        "template_context_variables": template_vars,
        "call_disposition_codes": workflow.call_disposition_codes,
        "workflow_configurations": workflow_configs,
        "version_number": active_def.version_number if active_def else None,
        "version_status": active_def.status if active_def else None,
        "workflow_uuid": workflow.workflow_uuid,
    }


@router.get("/{workflow_id}/versions")
async def get_workflow_versions(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> list[WorkflowVersionResponse]:
    """List all versions for a workflow, newest first."""
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    versions = await db_client.get_workflow_versions(workflow_id)
    return [
        WorkflowVersionResponse(
            id=v.id,
            version_number=v.version_number,
            status=v.status,
            created_at=v.created_at,
            published_at=v.published_at,
            workflow_json=mask_workflow_definition(v.workflow_json),
            workflow_configurations=v.workflow_configurations,
            template_context_variables=v.template_context_variables,
        )
        for v in versions
        if v.version_number is not None
    ]


@router.post("/{workflow_id}/publish")
async def publish_workflow(
    workflow_id: int,
    user: UserModel = Depends(get_user),
):
    """Publish the current draft version of a workflow.

    Drafts are allowed to be incomplete (so the editor can save mid-edit),
    but a published version is what runtime executes — so this is the gate
    where the full DTO + graph + trigger-conflict checks must pass.
    """
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    draft = await db_client.get_draft_version(workflow_id)
    if draft is None:
        raise HTTPException(status_code=400, detail="No draft to publish")

    errors = await _validate_workflow_definition(
        draft.workflow_json, exclude_workflow_id=workflow_id
    )
    if errors:
        raise _validation_errors_http_exception(errors)

    try:
        published = await db_client.publish_workflow_draft(workflow_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.WORKFLOW_PUBLISHED,
        properties={
            "workflow_id": workflow_id,
            "version_number": published.version_number,
            "organization_id": user.selected_organization_id,
        },
    )

    return {
        "id": published.id,
        "version_number": published.version_number,
        "status": published.status,
        "published_at": published.published_at,
    }


@router.post("/{workflow_id}/create-draft")
async def create_workflow_draft(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> WorkflowVersionResponse:
    """Create a draft version from the current published version.

    If a draft already exists, returns the existing draft.
    """
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    draft = await db_client.save_workflow_draft(workflow_id)
    return WorkflowVersionResponse(
        id=draft.id,
        version_number=draft.version_number,
        status=draft.status,
        created_at=draft.created_at,
        published_at=draft.published_at,
        workflow_json=mask_workflow_definition(draft.workflow_json),
        workflow_configurations=draft.workflow_configurations,
        template_context_variables=draft.template_context_variables,
    )


@router.get("/summary")
async def get_workflows_summary(
    user: UserModel = Depends(get_user),
    status: Optional[str] = Query(
        None,
        description="Filter by status (e.g. 'active' or 'archived'). Omit to return all.",
    ),
) -> List[WorkflowSummaryResponse]:
    """Get minimal workflow information (id and name only) for all workflows"""
    workflows = await db_client.get_all_workflows(
        organization_id=user.selected_organization_id,
        status=status,
    )
    return [
        WorkflowSummaryResponse(id=workflow.id, name=workflow.name)
        for workflow in workflows
    ]


@router.put("/{workflow_id}/status")
async def update_workflow_status(
    workflow_id: int,
    request: UpdateWorkflowStatusRequest,
    user: UserModel = Depends(get_user),
) -> WorkflowResponse:
    """
    Update the status of a workflow (e.g., archive/unarchive).

    Args:
        workflow_id: The ID of the workflow to update
        request: The status update request

    Returns:
        The updated workflow
    """
    try:
        workflow = await db_client.update_workflow_status(
            workflow_id=workflow_id,
            status=request.status,
            organization_id=user.selected_organization_id,
        )
        run_count = await db_client.get_workflow_run_count(workflow.id)
        return {
            "id": workflow.id,
            "name": workflow.name,
            "status": workflow.status,
            "created_at": workflow.created_at,
            "workflow_definition": mask_workflow_definition(
                workflow.released_definition.workflow_json
            ),
            "current_definition_id": workflow.current_definition_id,
            "template_context_variables": workflow.template_context_variables,
            "call_disposition_codes": workflow.call_disposition_codes,
            "workflow_configurations": workflow.workflow_configurations,
            "total_runs": run_count,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{workflow_id}",
    **sdk_expose(
        method="update_workflow",
        description="Update a workflow's name and/or definition. Saves as a new draft.",
    ),
)
async def update_workflow(
    workflow_id: int,
    request: UpdateWorkflowRequest,
    user: UserModel = Depends(get_user),
) -> WorkflowResponse:
    """
    Update an existing workflow.

    Args:
        workflow_id: The ID of the workflow to update
        request: The update request containing the new name and workflow definition

    Returns:
        The updated workflow

    Raises:
        HTTPException: If the workflow is not found or if there's a database error
    """
    try:
        # Strip UI runtime-only fields (invalid, validationMessage, etc.) from
        # node.data / edge.data before anything touches the DB — the UI sends
        # nodes wholesale from the React Flow store, which carries those.
        workflow_definition = sanitize_workflow_definition(request.workflow_definition)
        # Mint trigger_path for any trigger node that ships without one. The
        # response echoes workflow_definition so the client picks up the new
        # UUID without a refetch.
        workflow_definition = ensure_trigger_paths(workflow_definition)
        if workflow_definition:
            existing_workflow = await db_client.get_workflow(
                workflow_id, organization_id=user.selected_organization_id
            )
            if existing_workflow:
                # Merge against what the user was editing (draft or published)
                existing_draft = await db_client.get_draft_version(workflow_id)
                existing_def = (
                    existing_draft.workflow_json
                    if existing_draft
                    else existing_workflow.released_definition.workflow_json
                )
                workflow_definition = merge_workflow_api_keys(
                    workflow_definition,
                    existing_def,
                )

        # Validate model_overrides: resolve onto global config, then
        # run the same validator used by the user-configurations endpoint.
        if request.workflow_configurations and request.workflow_configurations.get(
            "model_overrides"
        ):
            user_config = await db_client.get_user_configurations(user.id)
            try:
                effective = resolve_effective_config(
                    user_config,
                    request.workflow_configurations["model_overrides"],
                )
                await UserConfigurationValidator().validate(
                    effective,
                    organization_id=user.selected_organization_id,
                    created_by=user.provider_id,
                )
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

        # Reject upfront if any new trigger path collides with another
        # workflow's trigger — keeps the workflow record from
        # being updated when the trigger sync would fail.
        if workflow_definition:
            new_trigger_paths = extract_trigger_paths(workflow_definition)
            if new_trigger_paths:
                try:
                    await db_client.assert_trigger_paths_available(
                        trigger_paths=new_trigger_paths,
                        exclude_workflow_id=workflow_id,
                    )
                except TriggerPathConflictError as e:
                    raise _trigger_conflict_http_exception(
                        workflow_definition, e.trigger_paths
                    )

        workflow = await db_client.update_workflow(
            workflow_id=workflow_id,
            name=request.name,
            workflow_definition=workflow_definition,
            template_context_variables=request.template_context_variables,
            workflow_configurations=request.workflow_configurations,
            organization_id=user.selected_organization_id,
        )

        # Sync agent triggers if workflow definition was updated
        if workflow_definition:
            trigger_paths = extract_trigger_paths(workflow_definition)
            await db_client.sync_triggers_for_workflow(
                workflow_id=workflow.id,
                organization_id=user.selected_organization_id,
                trigger_paths=trigger_paths,
            )

        # Return draft content if one exists (save creates a draft)
        draft = await db_client.get_draft_version(workflow_id)
        if draft:
            workflow_def = draft.workflow_json
            workflow_configs = draft.workflow_configurations
            template_vars = draft.template_context_variables
        else:
            published = workflow.released_definition
            workflow_def = published.workflow_json
            workflow_configs = published.workflow_configurations
            template_vars = published.template_context_variables

        # Include version info from the active definition (draft or published)
        active_def = draft or workflow.released_definition
        return {
            "id": workflow.id,
            "name": workflow.name,
            "status": workflow.status,
            "created_at": workflow.created_at,
            "workflow_definition": mask_workflow_definition(workflow_def),
            "current_definition_id": workflow.current_definition_id,
            "template_context_variables": template_vars,
            "call_disposition_codes": workflow.call_disposition_codes,
            "workflow_configurations": workflow_configs,
            "version_number": active_def.version_number if active_def else None,
            "version_status": active_def.status if active_def else None,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{workflow_id}/duplicate")
async def duplicate_workflow_endpoint(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> WorkflowResponse:
    """Duplicate a workflow including its definition, configuration, recordings, and triggers."""
    try:
        workflow = await duplicate_workflow(
            workflow_id=workflow_id,
            organization_id=user.selected_organization_id,
            user_id=user.id,
        )

        capture_event(
            distinct_id=str(user.provider_id),
            event=PostHogEvent.WORKFLOW_DUPLICATED,
            properties={
                "workflow_id": workflow.id,
                "workflow_name": workflow.name,
                "source_workflow_id": workflow_id,
                "organization_id": user.selected_organization_id,
            },
        )

        return {
            "id": workflow.id,
            "name": workflow.name,
            "status": workflow.status,
            "created_at": workflow.created_at,
            "workflow_definition": mask_workflow_definition(
                workflow.released_definition.workflow_json
            ),
            "current_definition_id": workflow.current_definition_id,
            "template_context_variables": workflow.template_context_variables,
            "call_disposition_codes": workflow.call_disposition_codes,
            "workflow_configurations": workflow.workflow_configurations,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error duplicating workflow {workflow_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{workflow_id}/runs")
async def create_workflow_run(
    workflow_id: int,
    request: CreateWorkflowRunRequest,
    user: UserModel = Depends(get_user),
) -> CreateWorkflowRunResponse:
    """
    Create a new workflow run when the user decides to execute the workflow via chat or voice

    Args:
        workflow_id: The ID of the workflow to run
        request: The create workflow run request
        user: The user to create the workflow run for
    """
    run = await db_client.create_workflow_run(
        request.name, workflow_id, request.mode, user.id, use_draft=True
    )
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "name": run.name,
        "mode": run.mode,
        "created_at": run.created_at,
        "definition_id": run.definition_id,
        "initial_context": run.initial_context,
        "gathered_context": run.gathered_context,
    }


@router.get("/{workflow_id}/runs/{run_id}")
async def get_workflow_run(
    workflow_id: int, run_id: int, user: UserModel = Depends(get_user)
) -> WorkflowRunResponseSchema:
    run = await db_client.get_workflow_run(
        run_id, organization_id=user.selected_organization_id
    )
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "name": run.name,
        "mode": run.mode,
        "is_completed": run.is_completed,
        "transcript_url": run.transcript_url,
        "recording_url": run.recording_url,
        "cost_info": {
            "dograh_token_usage": (
                run.cost_info.get("dograh_token_usage")
                if run.cost_info and "dograh_token_usage" in run.cost_info
                else round(float(run.cost_info.get("total_cost_usd", 0)) * 100, 2)
                if run.cost_info and "total_cost_usd" in run.cost_info
                else 0
            ),
            "call_duration_seconds": int(
                round(run.cost_info.get("call_duration_seconds"))
            )
            if run.cost_info and run.cost_info.get("call_duration_seconds") is not None
            else None,
        }
        if run.cost_info
        else None,
        "created_at": run.created_at,
        "definition_id": run.definition_id,
        "initial_context": run.initial_context,
        "gathered_context": run.gathered_context,
        "call_type": run.call_type,
        "logs": run.logs,
        "annotations": run.annotations,
    }


class WorkflowRunsResponse(BaseModel):
    runs: List[WorkflowRunResponseSchema]
    total_count: int
    page: int
    limit: int
    total_pages: int
    applied_filters: Optional[List[dict]] = None


@router.get("/{workflow_id}/runs")
async def get_workflow_runs(
    workflow_id: int,
    page: int = 1,
    limit: int = 50,
    filters: Optional[str] = Query(None, description="JSON-encoded filter criteria"),
    sort_by: Optional[str] = Query(
        None, description="Field to sort by (e.g., 'duration', 'created_at')"
    ),
    sort_order: Optional[str] = Query(
        "desc", description="Sort order ('asc' or 'desc')"
    ),
    user: UserModel = Depends(get_user),
) -> WorkflowRunsResponse:
    """
    Get workflow runs with optional filtering and sorting.

    Filters should be provided as a JSON-encoded array of filter criteria.
    Example: [{"attribute": "dateRange", "value": {"from": "2024-01-01", "to": "2024-01-31"}}]
    """
    offset = (page - 1) * limit

    # Parse filters if provided
    filter_criteria = []
    if filters:
        try:
            filter_criteria = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filter format")

        # Restrict allowed filter attributes for regular users
        allowed_attributes = {
            "dateRange",
            "dispositionCode",
            "duration",
            "status",
            "tokenUsage",
        }
        for filter_item in filter_criteria:
            attribute = filter_item.get("attribute")
            if attribute and attribute not in allowed_attributes:
                raise HTTPException(
                    status_code=403, detail=f"Invalid attribute '{attribute}'"
                )

    runs, total_count = await db_client.get_workflow_runs_by_workflow_id(
        workflow_id,
        organization_id=user.selected_organization_id,
        limit=limit,
        offset=offset,
        filters=filter_criteria if filter_criteria else None,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    total_pages = (total_count + limit - 1) // limit

    return WorkflowRunsResponse(
        runs=runs,
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
        applied_filters=filter_criteria if filter_criteria else None,
    )


@router.get("/{workflow_id}/report")
async def download_workflow_report(
    workflow_id: int,
    user: UserModel = Depends(get_user),
    start_date: Optional[datetime] = Query(
        None, description="Filter runs created on or after this datetime (ISO 8601)"
    ),
    end_date: Optional[datetime] = Query(
        None, description="Filter runs created on or before this datetime (ISO 8601)"
    ),
) -> StreamingResponse:
    """Download a CSV report of completed runs for a workflow."""
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    output, filename = await generate_workflow_report_csv(
        workflow_id, start_date=start_date, end_date=end_date
    )

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/templates")
async def get_workflow_templates() -> List[WorkflowTemplateResponse]:
    """
    Get all available workflow templates.

    Returns:
        List of workflow templates
    """
    template_client = WorkflowTemplateClient()
    templates = await template_client.get_all_workflow_templates()

    return [
        {
            "id": template.id,
            "template_name": template.template_name,
            "template_description": template.template_description,
            "template_json": template.template_json,
            "created_at": template.created_at,
        }
        for template in templates
    ]


@router.post("/templates/duplicate")
async def duplicate_workflow_template(
    request: DuplicateTemplateRequest, user: UserModel = Depends(get_user)
) -> WorkflowResponse:
    """
    Duplicate a workflow template to create a new workflow for the user.

    Args:
        request: The duplicate template request
        user: The authenticated user

    Returns:
        The newly created workflow
    """
    template_client = WorkflowTemplateClient()
    template = await template_client.get_workflow_template(request.template_id)

    if not template:
        raise HTTPException(
            status_code=404,
            detail=f"Workflow template with id {request.template_id} not found",
        )

    # Create a new workflow from the template
    # Regenerate trigger UUIDs to avoid conflicts with existing triggers
    workflow_def = regenerate_trigger_uuids(template.template_json)

    trigger_paths = extract_trigger_paths(workflow_def) if workflow_def else []
    if trigger_paths:
        try:
            await db_client.assert_trigger_paths_available(
                trigger_paths=trigger_paths,
            )
        except TriggerPathConflictError as e:
            raise HTTPException(status_code=409, detail=str(e))

    workflow = await db_client.create_workflow(
        request.workflow_name,
        workflow_def,
        user.id,
        user.selected_organization_id,
    )

    if trigger_paths:
        await db_client.sync_triggers_for_workflow(
            workflow_id=workflow.id,
            organization_id=user.selected_organization_id,
            trigger_paths=trigger_paths,
        )

    return {
        "id": workflow.id,
        "name": workflow.name,
        "status": workflow.status,
        "created_at": workflow.created_at,
        "workflow_definition": mask_workflow_definition(workflow_def),
        "current_definition_id": workflow.current_definition_id,
        "template_context_variables": workflow.template_context_variables,
        "call_disposition_codes": workflow.call_disposition_codes,
        "workflow_configurations": workflow.workflow_configurations,
    }


# ---------------------------------------------------------------------------
# Ambient Noise Upload
# ---------------------------------------------------------------------------


class AmbientNoiseUploadRequest(BaseModel):
    workflow_id: int
    filename: str
    mime_type: str = "audio/wav"
    file_size: int = Field(..., gt=0, le=10_485_760, description="Max 10MB")


class AmbientNoiseUploadResponse(BaseModel):
    upload_url: str
    storage_key: str
    storage_backend: str


@router.post(
    "/ambient-noise/upload-url",
    response_model=AmbientNoiseUploadResponse,
    summary="Get a presigned URL to upload a custom ambient noise audio file",
)
async def get_ambient_noise_upload_url(
    request: AmbientNoiseUploadRequest,
    user=Depends(get_user),
):
    """Generate a presigned PUT URL for uploading a custom ambient noise file."""
    # Verify user owns this workflow
    workflow = await db_client.get_workflow(
        request.workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", request.filename)
    storage_key = (
        f"ambient-noise/{user.selected_organization_id}"
        f"/{request.workflow_id}/{uuid.uuid4()}_{sanitized}"
    )

    upload_url = await storage_fs.aget_presigned_put_url(
        file_path=storage_key,
        expiration=1800,
        content_type=request.mime_type,
        max_size=request.file_size,
    )
    if not upload_url:
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")

    return AmbientNoiseUploadResponse(
        upload_url=upload_url,
        storage_key=storage_key,
        storage_backend=StorageBackend.get_current_backend().value,
    )
