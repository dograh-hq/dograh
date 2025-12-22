import json
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from httpx import HTTPStatusError
from loguru import logger
from pydantic import BaseModel, ValidationError

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.db.models import UserModel
from api.db.workflow_template_client import WorkflowTemplateClient
from api.schemas.workflow import WorkflowRunResponseSchema
from api.services.auth.depends import get_user
from api.services.mps_service_key_client import mps_service_key_client
from api.services.workflow.dto import ReactFlowDTO
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


router = APIRouter(prefix="/workflow")


class ValidateWorkflowResponse(BaseModel):
    is_valid: bool
    errors: list[WorkflowError]


class WorkflowResponse(BaseModel):
    id: int
    name: str
    status: str
    created_at: datetime
    workflow_definition: dict
    current_definition_id: int | None
    template_context_variables: dict | None = None
    call_disposition_codes: dict | None = None
    total_runs: int | None = None
    workflow_configurations: dict | None = None


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
    name: str
    workflow_definition: dict | None = None
    template_context_variables: dict | None = None
    workflow_configurations: dict | None = None


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
    call_type: Literal["INBOUND", "OUTBOUND"]
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

    errors: list[WorkflowError] = []

    # Get workflow definition from WorkflowDefinition table, fallback to workflow_definition field
    workflow_definition = workflow.workflow_definition_with_fallback

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

    if errors:
        raise HTTPException(
            status_code=422,
            detail=ValidateWorkflowResponse(is_valid=False, errors=errors).model_dump(),
        )

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


@router.post("/create/definition")
async def create_workflow(
    request: CreateWorkflowRequest, user: UserModel = Depends(get_user)
) -> WorkflowResponse:
    """
    Create a new workflow from the client

    Args:
        request: The create workflow request
        user: The user to create the workflow for
    """
    workflow = await db_client.create_workflow(
        request.name,
        request.workflow_definition,
        user.id,
        user.selected_organization_id,
    )

    # Sync agent triggers if workflow definition contains any
    if request.workflow_definition:
        trigger_paths = extract_trigger_paths(request.workflow_definition)
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
        "workflow_definition": workflow.workflow_definition_with_fallback,
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
                call_type=request.call_type,
                use_case=request.use_case,
                activity_description=request.activity_description,
                created_by=str(user.provider_id),
            )
        else:
            if not user.selected_organization_id:
                raise HTTPException(status_code=400, detail="No organization selected")

            workflow_data = await mps_service_key_client.call_workflow_api(
                call_type=request.call_type,
                use_case=request.use_case,
                activity_description=request.activity_description,
                organization_id=user.selected_organization_id,
            )

        # Create the workflow in our database
        # Regenerate trigger UUIDs to avoid conflicts with existing triggers
        workflow_def = regenerate_trigger_uuids(
            workflow_data.get("workflow_definition", {})
        )
        workflow = await db_client.create_workflow(
            name=workflow_data.get("name", f"{request.use_case} - {request.call_type}"),
            workflow_definition=workflow_def,
            user_id=user.id,
            organization_id=user.selected_organization_id,
        )

        # Sync agent triggers if workflow definition contains any
        if workflow_def:
            trigger_paths = extract_trigger_paths(workflow_def)
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
            "workflow_definition": workflow.workflow_definition_with_fallback,
            "current_definition_id": workflow.current_definition_id,
            "template_context_variables": workflow.template_context_variables,
            "call_disposition_codes": workflow.call_disposition_codes,
            "workflow_configurations": workflow.workflow_configurations,
        }

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


@router.get("/fetch")
async def get_workflows(
    user: UserModel = Depends(get_user),
    status: Optional[str] = Query(
        None,
        description="Filter by status - can be single value (active/archived) or comma-separated (active,archived)",
    ),
) -> List[WorkflowResponse]:
    """Get all workflows for the authenticated user's organization"""
    # Handle comma-separated status values
    if status and "," in status:
        # Split comma-separated values and fetch workflows for each status
        status_list = [s.strip() for s in status.split(",")]
        all_workflows = []
        for status_value in status_list:
            workflows = await db_client.get_all_workflows(
                organization_id=user.selected_organization_id, status=status_value
            )
            all_workflows.extend(workflows)
        workflows = all_workflows
    else:
        # Single status or no status filter
        workflows = await db_client.get_all_workflows(
            organization_id=user.selected_organization_id, status=status
        )

    # Get run counts for each workflow
    workflow_responses = []
    for workflow in workflows:
        run_count = await db_client.get_workflow_run_count(workflow.id)
        workflow_responses.append(
            {
                "id": workflow.id,
                "name": workflow.name,
                "status": workflow.status,
                "created_at": workflow.created_at,
                "workflow_definition": workflow.workflow_definition_with_fallback,
                "current_definition_id": workflow.current_definition_id,
                "template_context_variables": workflow.template_context_variables,
                "call_disposition_codes": workflow.call_disposition_codes,
                "workflow_configurations": workflow.workflow_configurations,
                "total_runs": run_count,
            }
        )

    return workflow_responses


@router.get("/fetch/{workflow_id}")
async def get_workflow(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> WorkflowResponse:
    """Get a single workflow by ID"""
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    return {
        "id": workflow.id,
        "name": workflow.name,
        "status": workflow.status,
        "created_at": workflow.created_at,
        "workflow_definition": workflow.workflow_definition_with_fallback,
        "current_definition_id": workflow.current_definition_id,
        "template_context_variables": workflow.template_context_variables,
        "call_disposition_codes": workflow.call_disposition_codes,
        "workflow_configurations": workflow.workflow_configurations,
    }


@router.get("/summary")
async def get_workflows_summary(
    user: UserModel = Depends(get_user),
) -> List[WorkflowSummaryResponse]:
    """Get minimal workflow information (id and name only) for all workflows"""
    workflows = await db_client.get_all_workflows(
        organization_id=user.selected_organization_id
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
            "workflow_definition": workflow.workflow_definition_with_fallback,
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


@router.put("/{workflow_id}")
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
        workflow = await db_client.update_workflow(
            workflow_id=workflow_id,
            name=request.name,
            workflow_definition=request.workflow_definition,
            template_context_variables=request.template_context_variables,
            workflow_configurations=request.workflow_configurations,
            organization_id=user.selected_organization_id,
        )

        # Sync agent triggers if workflow definition was updated
        if request.workflow_definition:
            trigger_paths = extract_trigger_paths(request.workflow_definition)
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
            "workflow_definition": workflow.workflow_definition_with_fallback,
            "current_definition_id": workflow.current_definition_id,
            "template_context_variables": workflow.template_context_variables,
            "call_disposition_codes": workflow.call_disposition_codes,
            "workflow_configurations": workflow.workflow_configurations,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
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
        request.name, workflow_id, request.mode, user.id
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
            if run.cost_info
            else None,
        }
        if run.cost_info
        else None,
        "created_at": run.created_at,
        "definition_id": run.definition_id,
        "initial_context": run.initial_context,
        "gathered_context": run.gathered_context,
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
    user: UserModel = Depends(get_user),
) -> WorkflowRunsResponse:
    """
    Get workflow runs with optional filtering.

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

    # Apply filters if any
    if filter_criteria:
        runs, total_count = await db_client.get_workflow_runs_by_workflow_id(
            workflow_id,
            organization_id=user.selected_organization_id,
            limit=limit,
            offset=offset,
            filters=filter_criteria,
        )
    else:
        # Use existing logic for unfiltered results
        runs, total_count = await db_client.get_workflow_runs_by_workflow_id(
            workflow_id,
            organization_id=user.selected_organization_id,
            limit=limit,
            offset=offset,
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
    workflow = await db_client.create_workflow(
        request.workflow_name,
        workflow_def,
        user.id,
        user.selected_organization_id,
    )

    # Sync agent triggers if template contains any
    if workflow_def:
        trigger_paths = extract_trigger_paths(workflow_def)
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
        "workflow_definition": workflow.workflow_definition_with_fallback,
        "current_definition_id": workflow.current_definition_id,
        "template_context_variables": workflow.template_context_variables,
        "call_disposition_codes": workflow.call_disposition_codes,
        "workflow_configurations": workflow.workflow_configurations,
    }
