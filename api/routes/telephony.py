"""
Generic telephony routes that work with any telephony provider.
"""
import json
import random
from datetime import UTC, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, WebSocket
from loguru import logger
from pydantic import BaseModel
from starlette.responses import HTMLResponse

from api.db import db_client
from api.db.models import UserModel
from api.enums import WorkflowRunMode
from api.services.auth.depends import get_user
from api.services.campaign.call_dispatcher import campaign_call_dispatcher
from api.services.campaign.campaign_event_publisher import get_campaign_event_publisher
from api.services.pipecat.run_pipeline import run_pipeline_twilio
from api.services.telephony.factory import get_telephony_provider
from api.utils.tunnel import TunnelURLProvider
from pipecat.utils.context import set_current_run_id

router = APIRouter(prefix="/telephony")


class InitiateCallRequest(BaseModel):
    workflow_id: int
    workflow_run_id: int | None = None


class StatusCallbackRequest(BaseModel):
    """Generic status callback that can handle different providers"""
    # Common fields
    call_id: str
    status: str
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    direction: Optional[str] = None
    duration: Optional[str] = None
    
    # Provider-specific fields stored as extra
    extra: dict = {}
    
    @classmethod
    def from_twilio(cls, data: dict):
        """Convert Twilio callback to generic format"""
        return cls(
            call_id=data.get("CallSid", ""),
            status=data.get("CallStatus", ""),
            from_number=data.get("From"),
            to_number=data.get("To"),
            direction=data.get("Direction"),
            duration=data.get("CallDuration") or data.get("Duration"),
            extra=data
        )


@router.post("/initiate-call")
async def initiate_call(
    request: InitiateCallRequest, user: UserModel = Depends(get_user)
):
    """Initiate a call using the configured telephony provider."""
    
    # Get the telephony provider for the organization
    provider = await get_telephony_provider(user.selected_organization_id)
    
    # Validate provider is configured
    if not provider.validate_config():
        raise HTTPException(
            status_code=400,
            detail="telephony_not_configured",
        )
    
    user_configuration = await db_client.get_user_configurations(user.id)
    
    workflow_run_id = request.workflow_run_id
    
    if not workflow_run_id:
        workflow_run_name = f"WR-TEL-{random.randint(1000, 9999)}"
        workflow_run = await db_client.create_workflow_run(
            workflow_run_name,
            request.workflow_id,
            WorkflowRunMode.TWILIO.value,  # TODO: Make this provider-agnostic
            initial_context={
                "phone_number": user_configuration.test_phone_number,
            },
            user_id=user.id,
        )
        workflow_run_id = workflow_run.id
    else:
        workflow_run = await db_client.get_workflow_run(workflow_run_id, user.id)
        if not workflow_run:
            raise HTTPException(status_code=400, detail="Workflow run not found")
        workflow_run_name = workflow_run.name
    
    if not user_configuration.test_phone_number:
        raise HTTPException(status_code=400, detail="Test phone number not set")
    
    # Construct webhook URL
    backend_endpoint = await TunnelURLProvider.get_tunnel_url()
    webhook_url = (
        f"https://{backend_endpoint}/api/v1/telephony/twiml"
        f"?workflow_id={request.workflow_id}"
        f"&user_id={user.id}"
        f"&workflow_run_id={workflow_run_id}"
        f"&organization_id={user.selected_organization_id}"
    )
    
    # Initiate call via provider
    await provider.initiate_call(
        to_number=user_configuration.test_phone_number,
        webhook_url=webhook_url,
        workflow_run_id=workflow_run_id,
    )
    
    return {
        "message": f"Call initiated successfully with run name {workflow_run_name}"
    }


@router.post("/twiml", include_in_schema=False)
async def handle_twiml_webhook(
    workflow_id: int,
    user_id: int, 
    workflow_run_id: int,
    organization_id: int
):
    """
    Handle initial webhook from telephony provider.
    Returns provider-specific response (e.g., TwiML for Twilio).
    """
    # Get provider for organization - exactly like original gets TwilioService
    provider = await get_telephony_provider(organization_id)
    
    # Generate provider-specific response (TwiML for Twilio)
    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )
    
    # Return exactly like original - HTMLResponse with application/xml
    return HTMLResponse(content=response_content, media_type="application/xml")


@router.websocket("/ws/{workflow_id}/{user_id}/{workflow_run_id}")
async def websocket_endpoint(
    websocket: WebSocket, workflow_id: int, user_id: int, workflow_run_id: int
):
    """WebSocket endpoint for real-time call handling - matches original Twilio implementation."""
    await websocket.accept()

    try:
        # "connected" (ignore)
        msg = json.loads(await websocket.receive_text())
        if msg.get("event") != "connected":
            raise RuntimeError("Expected connected message first")

        # "start" – this has everything we need
        start_msg = await websocket.receive_text()

        # set the run context
        set_current_run_id(workflow_run_id)

        logger.debug(f"Received start message: {start_msg}")

        start_msg = json.loads(start_msg)
        if start_msg.get("event") != "start":
            raise RuntimeError("Expected start message second")

        try:
            stream_sid = start_msg["start"]["streamSid"]
            call_sid = start_msg["start"]["callSid"]
        except KeyError:
            logger.error(
                "Missing callSID and streamSID in start message. Closing connection."
            )
            await websocket.close(code=4400, reason="Missing or bad start message")
            return

        # Run your Pipecat bot
        await run_pipeline_twilio(
            websocket, stream_sid, call_sid, workflow_id, workflow_run_id, user_id
        )
    except Exception as e:
        logger.error(f"Error in Twilio WebSocket connection: {e}")
        await websocket.close(1011, "Internal server error")


@router.post("/status-callback/{workflow_run_id}")
async def handle_status_callback(
    workflow_run_id: int,
    request: Request,
    x_twilio_signature: Optional[str] = Header(None),
):
    """Handle status callbacks from telephony providers."""
    
    # Parse form data
    form_data = await request.form()
    callback_data = dict(form_data)
    
    logger.info(
        f"[run {workflow_run_id}] Received status callback: {json.dumps(callback_data)}"
    )
    
    # Get workflow run to find organization
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(f"Workflow run {workflow_run_id} not found for status callback")
        return {"status": "ignored", "reason": "workflow_run_not_found"}
    
    # Get provider for verification (if signature provided)
    if x_twilio_signature:
        # Get organization from workflow run
        workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
        if workflow:
            provider = await get_telephony_provider(workflow.organization_id)
            
            # Verify signature
            backend_endpoint = await TunnelURLProvider.get_tunnel_url()
            full_url = f"https://{backend_endpoint}/api/v1/telephony/status-callback/{workflow_run_id}"
            
            is_valid = await provider.verify_webhook_signature(
                full_url, callback_data, x_twilio_signature
            )
            
            if not is_valid:
                logger.warning(f"Invalid status callback signature for run {workflow_run_id}")
                return {"status": "error", "reason": "invalid_signature"}
    
    # Convert provider-specific callback to generic format
    # (Currently assumes Twilio format, will be extended for other providers)
    status_update = StatusCallbackRequest.from_twilio(callback_data)
    
    # Process the status update
    await _process_status_update(workflow_run_id, status_update, workflow_run)
    
    return {"status": "success"}


async def _process_status_update(
    workflow_run_id: int,
    status: StatusCallbackRequest,
    workflow_run: any
):
    """Process status updates from telephony providers."""
    
    # Log the status callback
    twilio_callback_logs = workflow_run.logs.get("twilio_status_callbacks", [])
    twilio_callback_log = {
        "status": status.status,
        "timestamp": datetime.now(UTC).isoformat(),
        "call_id": status.call_id,
        "duration": status.duration,
        **status.extra  # Include provider-specific data
    }
    twilio_callback_logs.append(twilio_callback_log)
    
    # Update workflow run logs
    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        logs={"twilio_status_callbacks": twilio_callback_logs},
    )
    
    # Handle call completion
    if status.status == "completed":
        logger.info(
            f"[run {workflow_run_id}] Call completed with duration: {status.duration}s"
        )
        
        # Release concurrent slot if this was a campaign call
        if workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)
        
        # Mark workflow run as completed
        await db_client.update_workflow_run(
            run_id=workflow_run_id, is_completed=True
        )
        
        # Publish campaign event if applicable
        if workflow_run.campaign_id:
            publisher = await get_campaign_event_publisher()
            await publisher.publish_call_completed(
                campaign_id=workflow_run.campaign_id,
                workflow_run_id=workflow_run_id,
                queued_run_id=workflow_run.queued_run_id,
                call_duration=int(status.duration) if status.duration else 0,
            )
    
    elif status.status in ["failed", "busy", "no-answer", "canceled"]:
        logger.warning(f"[run {workflow_run_id}] Call failed with status: {status.status}")
        
        # Release concurrent slot for terminal statuses if this was a campaign call
        if workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)
        
        # Check if retry is needed for campaign calls (busy/no-answer)
        if status.status in ["busy", "no-answer"] and workflow_run.campaign_id:
            publisher = await get_campaign_event_publisher()
            await publisher.publish_retry_needed(
                workflow_run_id=workflow_run_id,
                reason=status.status.replace("-", "_"),  # Convert no-answer to no_answer
                campaign_id=workflow_run.campaign_id,
                queued_run_id=workflow_run.queued_run_id,
            )
        
        # Mark workflow run as completed with failure tags
        call_tags = workflow_run.gathered_context.get("call_tags", []) if workflow_run.gathered_context else []
        call_tags.extend(["not_connected", f"telephony_{status.status.lower()}"])
        
        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            is_completed=True,
            gathered_context={"call_tags": call_tags}
        )