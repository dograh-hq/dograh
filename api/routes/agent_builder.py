from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.db.models import UserModel
from api.enums import PostHogEvent
from api.services.agent_builder.service import (
    AgentBuilderError,
    BusinessInfo,
    create_agent_workflow,
    list_templates,
)
from api.services.auth.depends import get_user
from api.services.posthog_client import capture_event

router = APIRouter(prefix="/agent-builder")


class AgentTemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    fields: list[str]


class CreateAgentRequest(BaseModel):
    mode: Literal["describe", "template"]
    description: Optional[str] = None
    template_id: Optional[str] = None
    business: BusinessInfo


class CreateAgentResponse(BaseModel):
    workflow_id: int
    name: str


@router.get("/templates")
async def get_agent_templates(
    user: UserModel = Depends(get_user),
) -> List[AgentTemplateResponse]:
    """List the built-in agent templates (id, name, description, fields)."""
    return [AgentTemplateResponse(**t) for t in list_templates()]


@router.post("/create")
async def create_agent(
    request: CreateAgentRequest,
    user: UserModel = Depends(get_user),
) -> CreateAgentResponse:
    """Create a working voice agent from a description or a template.

    Builds a minimal valid workflow (start → agent → end) owned by the
    caller's organization and returns its id for redirecting to the editor.
    """
    try:
        workflow = await create_agent_workflow(
            mode=request.mode,
            description=request.description,
            template_id=request.template_id,
            business=request.business,
            user_id=user.id,
            organization_id=user.selected_organization_id,
        )
    except AgentBuilderError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Agent builder failed to create workflow: {e}")
        raise HTTPException(status_code=500, detail="Failed to create agent")

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.WORKFLOW_CREATED,
        properties={
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "source": "agent_builder",
            "mode": request.mode,
            "template_id": request.template_id,
            "organization_id": user.selected_organization_id,
        },
    )

    return CreateAgentResponse(workflow_id=workflow.id, name=workflow.name)
