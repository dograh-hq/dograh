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
from api.services.pipecat.run_pipeline import run_pipeline_twilio, run_pipeline_vonage
from api.services.telephony.factory import get_telephony_provider
from api.utils.tunnel import TunnelURLProvider
from pipecat.utils.context import set_current_run_id

router = APIRouter(prefix="/telephony")


class InitiateCallRequest(BaseModel):
    workflow_id: int
    workflow_run_id: int | None = None
    phone_number: str | None = None  # Optional phone number to call


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
    
    @classmethod
    def from_vonage(cls, data: dict):
        """Convert Vonage event to generic format"""
        # Map Vonage status to common format
        status_map = {
            "started": "initiated",
            "ringing": "ringing",
            "answered": "answered", 
            "complete": "completed",
            "failed": "failed",
            "busy": "busy",
            "timeout": "no-answer",
            "rejected": "busy"
        }
        
        return cls(
            call_id=data.get("uuid", ""),
            status=status_map.get(data.get("status", ""), data.get("status", "")),
            from_number=data.get("from"),
            to_number=data.get("to"),
            direction=data.get("direction"),
            duration=data.get("duration"),
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
    
    # Determine the workflow run mode based on provider type
    from api.services.telephony.providers.twilio_provider import TwilioProvider
    from api.services.telephony.providers.vonage_provider import VonageProvider
    
    if isinstance(provider, TwilioProvider):
        workflow_run_mode = WorkflowRunMode.TWILIO.value
    elif isinstance(provider, VonageProvider):
        workflow_run_mode = WorkflowRunMode.VONAGE.value
    else:
        # Default to TWILIO for backward compatibility
        workflow_run_mode = WorkflowRunMode.TWILIO.value
    
    user_configuration = await db_client.get_user_configurations(user.id)
    
    # Use phone number from request, or fall back to user configuration
    phone_number = request.phone_number or user_configuration.test_phone_number
    
    if not phone_number:
        raise HTTPException(
            status_code=400, 
            detail="Phone number must be provided in request or set in user configuration"
        )
    
    workflow_run_id = request.workflow_run_id
    
    if not workflow_run_id:
        workflow_run_name = f"WR-TEL-{random.randint(1000, 9999)}"
        workflow_run = await db_client.create_workflow_run(
            workflow_run_name,
            request.workflow_id,
            workflow_run_mode,  # Now provider-agnostic
            initial_context={
                "phone_number": phone_number,
            },
            user_id=user.id,
        )
        workflow_run_id = workflow_run.id
    else:
        workflow_run = await db_client.get_workflow_run(workflow_run_id, user.id)
        if not workflow_run:
            raise HTTPException(status_code=400, detail="Workflow run not found")
        workflow_run_name = workflow_run.name
    
    # Construct webhook URL based on provider type
    backend_endpoint = await TunnelURLProvider.get_tunnel_url()
    
    # Check provider type to determine webhook endpoint
    provider_type = getattr(provider, '__class__', None).__name__ if provider else None
    webhook_endpoint = "ncco" if provider_type == "VonageProvider" else "twiml"
    
    webhook_url = (
        f"https://{backend_endpoint}/api/v1/telephony/{webhook_endpoint}"
        f"?workflow_id={request.workflow_id}"
        f"&user_id={user.id}"
        f"&workflow_run_id={workflow_run_id}"
        f"&organization_id={user.selected_organization_id}"
    )
    
    # Initiate call via provider
    result = await provider.initiate_call(
        to_number=phone_number,
        webhook_url=webhook_url,
        workflow_run_id=workflow_run_id,
    )
    
    # Store call UUID for Vonage in workflow run context
    if provider_type == "VonageProvider" and result and "uuid" in result:
        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            gathered_context={"call_uuid": result["uuid"]}
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


@router.get("/ncco", include_in_schema=False)
async def handle_ncco_webhook(
    workflow_id: int, 
    user_id: int, 
    workflow_run_id: int,
    organization_id: Optional[int] = None
):
    """Handle NCCO (Nexmo Call Control Objects) webhook for Vonage.
    
    Returns JSON response instead of XML like TwiML.
    """
    # Get provider for organization
    provider = await get_telephony_provider(organization_id or user_id)
    
    # Generate NCCO response (JSON for Vonage)
    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )
    
    # Return JSON response for Vonage
    return json.loads(response_content)


@router.websocket("/ws/{workflow_id}/{user_id}/{workflow_run_id}")
async def websocket_endpoint(
    websocket: WebSocket, workflow_id: int, user_id: int, workflow_run_id: int
):
    """WebSocket endpoint for real-time call handling - supports both Twilio and Vonage."""
    await websocket.accept()

    try:
        # set the run context
        set_current_run_id(workflow_run_id)
        
        # Peek at the first message to determine provider
        # Twilio sends JSON with "connected" event
        # Vonage sends binary audio directly or may send metadata
        first_msg = await websocket.receive()
        
        if "text" in first_msg:
            # Text message - likely Twilio
            msg = json.loads(first_msg["text"])
            if msg.get("event") == "connected":
                # Definitely Twilio - follow Twilio flow
                
                # "start" – this has everything we need
                start_msg = await websocket.receive_text()
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

                # Run Twilio pipeline
                await run_pipeline_twilio(
                    websocket, stream_sid, call_sid, workflow_id, workflow_run_id, user_id
                )
            elif msg.get("event") == "websocket:connected":
                # This is Vonage's initial connection message
                logger.info(f"Vonage WebSocket connected for workflow_run {workflow_run_id}")
                
                # Get workflow run to extract call UUID
                workflow_run = await db_client.get_workflow_run(workflow_run_id)
                workflow = await db_client.get_workflow(workflow_id)
                
                # Extract call UUID from workflow run context
                call_uuid = workflow_run.gathered_context.get("call_uuid") if workflow_run.gathered_context else None
                
                if not call_uuid:
                    logger.error("No call UUID found for Vonage connection")
                    await websocket.close(code=4400, reason="Missing call UUID")
                    return
                
                # Run Vonage pipeline
                await run_pipeline_vonage(
                    websocket, 
                    call_uuid,
                    workflow,
                    workflow.organization_id,
                    workflow_id,
                    workflow_run_id,
                    user_id
                )
            else:
                # Unknown provider or format
                logger.warning(f"Unknown first message format: {msg}")
                
        elif "bytes" in first_msg:
            # Binary message - likely Vonage audio
            # For Vonage, we need to get the call UUID from the workflow run
            workflow_run = await db_client.get_workflow_run(workflow_run_id)
            workflow = await db_client.get_workflow(workflow_id)
            
            # Extract call UUID from workflow run context
            call_uuid = workflow_run.gathered_context.get("call_uuid") if workflow_run.gathered_context else None
            
            if not call_uuid:
                logger.error("No call UUID found for Vonage connection")
                await websocket.close(code=4400, reason="Missing call UUID")
                return
            
            # Run Vonage pipeline
            await run_pipeline_vonage(
                websocket, 
                call_uuid,
                workflow,
                workflow.organization_id,  # Use the actual organization_id from workflow
                workflow_id,
                workflow_run_id,
                user_id
            )
            
    except Exception as e:
        logger.error(f"Error in WebSocket connection: {e}")
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
                logger.warning(f"Invalid webhook signature for workflow run {workflow_run_id}")
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


@router.post("/events/{workflow_run_id}")
async def handle_vonage_events(
    request: Request,
    workflow_run_id: int,
):
    """Handle Vonage event webhooks.
    
    Vonage sends all call events to a single endpoint.
    Events include: started, ringing, answered, complete, failed, etc.
    """
    # Parse the event data
    event_data = await request.json()
    logger.info(f"[run {workflow_run_id}] Received Vonage event: {event_data}")
    
    # Get workflow run for processing
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.error(f"[run {workflow_run_id}] Workflow run not found")
        return {"status": "error", "message": "Workflow run not found"}
    
    # If this is a completed call and includes cost info, capture it immediately
    if event_data.get("status") == "completed":
        # Vonage sometimes includes price info in the webhook
        if "price" in event_data or "rate" in event_data:
            try:
                if workflow_run.cost_info:
                    # Store immediate cost info if available
                    cost_info = workflow_run.cost_info.copy()
                    if "price" in event_data:
                        cost_info["vonage_webhook_price"] = float(event_data["price"])
                    if "rate" in event_data:
                        cost_info["vonage_webhook_rate"] = float(event_data["rate"])
                    if "duration" in event_data:
                        cost_info["vonage_webhook_duration"] = int(event_data["duration"])
                    
                    await db_client.update_workflow_run(
                        run_id=workflow_run_id,
                        cost_info=cost_info
                    )
                    logger.info(f"[run {workflow_run_id}] Captured Vonage cost info from webhook")
            except Exception as e:
                logger.error(f"[run {workflow_run_id}] Failed to capture Vonage cost from webhook: {e}")
    
    # Convert to generic status format
    status_update = StatusCallbackRequest.from_vonage(event_data)
    
    # Process the status update
    await _process_status_update(workflow_run_id, status_update, workflow_run)
    
    # Return 204 No Content as expected by Vonage
    return {"status": "ok"}