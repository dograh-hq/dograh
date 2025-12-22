"""Public API endpoints for agent triggers.

These endpoints are accessible with API key authentication and allow
external systems to programmatically trigger phone calls.
"""

import random
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.enums import TriggerState
from api.services.quota_service import check_dograh_quota_by_user_id
from api.services.telephony.factory import get_telephony_provider
from api.utils.tunnel import TunnelURLProvider

router = APIRouter(prefix="/public/agent")


class TriggerCallRequest(BaseModel):
    """Request model for triggering a call via API"""

    phone_number: str
    initial_context: Optional[dict] = None


class TriggerCallResponse(BaseModel):
    """Response model for successful call initiation"""

    status: str
    workflow_run_id: int
    workflow_run_name: str


def trigger_exists_in_workflow(workflow_definition: dict, trigger_path: str) -> bool:
    """Check if trigger node exists in workflow definition.

    Args:
        workflow_definition: The workflow definition JSON
        trigger_path: The trigger UUID to look for

    Returns:
        True if trigger node exists, False otherwise
    """
    nodes = workflow_definition.get("nodes", [])
    for node in nodes:
        if node.get("type") == "trigger":
            if node.get("data", {}).get("trigger_path") == trigger_path:
                return True
    return False


@router.post("/{uuid}", response_model=TriggerCallResponse)
async def initiate_call(
    uuid: str,
    request: TriggerCallRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Initiate a phone call via API trigger.

    This endpoint allows external systems (CRMs, automation tools, etc.) to
    programmatically trigger outbound phone calls with custom context variables.

    Args:
        uuid: The unique trigger UUID
        request: The call request with phone number and optional context
        x_api_key: API key for authentication (passed in X-API-Key header)

    Returns:
        TriggerCallResponse with workflow run details

    Raises:
        HTTPException: Various error conditions (401, 403, 404, 400)
    """
    # 1. Validate API key
    api_key = await db_client.validate_api_key(x_api_key)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 2. Lookup agent trigger by UUID
    trigger = await db_client.get_agent_trigger_by_path(uuid)
    if not trigger:
        raise HTTPException(status_code=404, detail="Agent trigger not found")

    # 3. Validate organization match (API key org must match trigger org)
    if api_key.organization_id != trigger.organization_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # 4. Validate trigger is active
    if trigger.state != TriggerState.ACTIVE.value:
        raise HTTPException(status_code=404, detail="Agent trigger is not active")

    # 4.5 Check Dograh quota before initiating the call
    quota_result = await check_dograh_quota_by_user_id(api_key.created_by)
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    # 5. Get workflow and validate trigger exists in definition
    workflow = await db_client.get_workflow_by_id(trigger.workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get workflow definition (with fallback to legacy field)
    workflow_definition = workflow.workflow_definition_with_fallback

    # Validate trigger node still exists in the workflow definition
    if not trigger_exists_in_workflow(workflow_definition, uuid):
        raise HTTPException(
            status_code=404,
            detail="Trigger not found or has been removed from workflow",
        )

    # 6. Get telephony provider for the organization
    provider = await get_telephony_provider(trigger.organization_id)

    # Validate provider is configured
    if not provider.validate_config():
        raise HTTPException(
            status_code=400,
            detail="Telephony provider not configured for this organization",
        )

    # 7. Determine the workflow run mode based on provider type
    workflow_run_mode = provider.PROVIDER_NAME

    # 8. Create workflow run
    workflow_run_name = f"WR-API-{random.randint(1000, 9999)}"
    workflow_run = await db_client.create_workflow_run(
        name=workflow_run_name,
        workflow_id=trigger.workflow_id,
        mode=workflow_run_mode,
        initial_context={
            "phone_number": request.phone_number,
            "agent_uuid": uuid,
            **(request.initial_context or {}),
        },
        user_id=api_key.created_by,
    )

    logger.info(
        f"Created workflow run {workflow_run.id} for API trigger {uuid} "
        f"to phone number {request.phone_number}"
    )

    # 9. Construct webhook URL for telephony provider callback
    backend_endpoint = await TunnelURLProvider.get_tunnel_url()
    webhook_endpoint = provider.WEBHOOK_ENDPOINT

    webhook_url = (
        f"https://{backend_endpoint}/api/v1/telephony/{webhook_endpoint}"
        f"?workflow_id={trigger.workflow_id}"
        f"&user_id={api_key.created_by}"
        f"&workflow_run_id={workflow_run.id}"
        f"&organization_id={trigger.organization_id}"
    )

    # 10. Initiate call via telephony provider
    result = await provider.initiate_call(
        to_number=request.phone_number,
        webhook_url=webhook_url,
        workflow_run_id=workflow_run.id,
    )

    # 11. Store provider metadata in workflow run context
    gathered_context = {
        "provider": provider.PROVIDER_NAME,
        "triggered_by": "api",
        "trigger_uuid": uuid,
        **(result.provider_metadata or {}),
    }
    await db_client.update_workflow_run(
        run_id=workflow_run.id, gathered_context=gathered_context
    )

    logger.info(
        f"Call initiated successfully for workflow run {workflow_run.id} "
        f"via trigger {uuid}"
    )

    return TriggerCallResponse(
        status="initiated",
        workflow_run_id=workflow_run.id,
        workflow_run_name=workflow_run_name,
    )
