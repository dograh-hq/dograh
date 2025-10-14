# TODO: Remove this entire file after migrating workflow_run_cost.py to use telephony abstraction
# All endpoints here are deprecated - use /api/v1/telephony/* instead

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
from api.enums import OrganizationConfigurationKey, WorkflowRunMode
from api.services.auth.depends import get_user
from api.services.campaign.call_dispatcher import campaign_call_dispatcher
from api.services.campaign.campaign_event_publisher import (
    get_campaign_event_publisher,
)
from api.services.pipecat.run_pipeline import run_pipeline_twilio
from api.services.telephony.twilio import TwilioService
from pipecat.utils.context import set_current_run_id

router = APIRouter(prefix="/twilio")


class InitiateCallRequest(BaseModel):
    workflow_id: int
    workflow_run_id: int | None = None


class TwilioStatusCallbackRequest(BaseModel):
    CallSid: str
    CallStatus: str
    From: Optional[str] = None
    To: Optional[str] = None
    Direction: Optional[str] = None
    Duration: Optional[str] = None
    CallDuration: Optional[str] = None
    RecordingUrl: Optional[str] = None
    RecordingSid: Optional[str] = None
    Timestamp: Optional[str] = None


@router.post("/initiate-call")
async def initiate_call(
    request: InitiateCallRequest, user: UserModel = Depends(get_user)
):
    # Check if organization has TWILIO_CONFIGURATION configured
    twilio_config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,
    )

    if not twilio_config or not twilio_config.value:
        raise HTTPException(
            status_code=400,
            detail="telephony_not_configured",  # Special error code
        )

    user_configuration = await db_client.get_user_configurations(user.id)

    workflow_run_id = request.workflow_run_id

    if not workflow_run_id:
        workflow_run_name = f"WR-TEL-{random.randint(1000, 9999)}"
        workflow_run = await db_client.create_workflow_run(
            workflow_run_name,
            request.workflow_id,
            WorkflowRunMode.TWILIO.value,
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

    if user_configuration.test_phone_number:
        twilio_service = TwilioService(user.selected_organization_id)
        await twilio_service.initiate_call(
            to_number=user_configuration.test_phone_number,
            url_args={
                "workflow_id": request.workflow_id,
                "user_id": user.id,
                "workflow_run_id": workflow_run_id,
                "organization_id": user.selected_organization_id,
            },
            workflow_run_id=workflow_run_id,
        )
        return {
            "message": f"Call initiated successfully with run name {workflow_run_name}"
        }
    else:
        raise HTTPException(status_code=400, detail="Test phone number not set")


@router.post("/twiml", include_in_schema=False)
async def start_call(
    workflow_id: int, user_id: int, workflow_run_id: int, organization_id: int
):
    twiml_content = await TwilioService(organization_id).get_start_call_twiml(
        workflow_id, user_id, workflow_run_id
    )
    return HTMLResponse(content=twiml_content, media_type="application/xml")


@router.websocket("/ws/{workflow_id}/{user_id}/{workflow_run_id}")
async def websocket_endpoint(
    websocket: WebSocket, workflow_id: int, user_id: int, workflow_run_id: int
):
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


@router.post("/status-callback/{workflow_run_id}", include_in_schema=False)
async def status_callback(
    request: Request,
    workflow_run_id: int,
    x_twilio_signature: Annotated[
        Optional[str], Header(alias="X-Twilio-Signature")
    ] = None,
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    From: Optional[str] = Form(None),
    To: Optional[str] = Form(None),
    Direction: Optional[str] = Form(None),
    Duration: Optional[str] = Form(None),
    CallDuration: Optional[str] = Form(None),
    RecordingUrl: Optional[str] = Form(None),
    RecordingSid: Optional[str] = Form(None),
    Timestamp: Optional[str] = Form(None),
):
    """Handle Twilio status callbacks for call lifecycle events."""
    try:
        # TODO: Implement Twilio signature verification

        # Create callback data object
        callback_data = {
            "CallSid": CallSid,
            "CallStatus": CallStatus,
            "From": From,
            "To": To,
            "Direction": Direction,
            "Duration": Duration,
            "CallDuration": CallDuration,
            "RecordingUrl": RecordingUrl,
            "RecordingSid": RecordingSid,
            "Timestamp": Timestamp,
        }

        # Remove None values for cleaner logging
        callback_data = {k: v for k, v in callback_data.items() if v is not None}

        logger.info(
            f"Received Twilio status callback for workflow_run_id {workflow_run_id}: {CallStatus}"
        )

        # Get the current workflow run
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            logger.error(f"Workflow run {workflow_run_id} not found for callback")
            return {"status": "error", "message": "Workflow run not found"}

        callback_logs = workflow_run.logs.get("twilio_status_callbacks", [])

        # Add new callback log entry to logs
        callback_log = {
            "status": CallStatus,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": callback_data,
        }
        callback_logs.append(callback_log)

        # Update the workflow run with the new logs
        await db_client.update_workflow_run(
            run_id=workflow_run_id, logs={"twilio_status_callbacks": callback_logs}
        )

        # Release concurrent slot when call ends (for any terminal status)
        terminal_statuses = ["completed", "busy", "no-answer", "failed", "canceled"]
        if CallStatus.lower() in terminal_statuses and workflow_run.campaign_id:
            # Release the concurrent slot for this call
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)

        # Check if retry is needed for campaign calls
        if (
            CallStatus.lower() in ["busy", "no-answer", "failed"]
            and workflow_run.campaign_id
        ):
            # Lets retry for busy and no-answer
            if CallStatus.lower() in ["busy", "no-answer"]:
                publisher = await get_campaign_event_publisher()
                await publisher.publish_retry_needed(
                    workflow_run_id=workflow_run_id,
                    reason=CallStatus.lower().replace(
                        "-", "_"
                    ),  # Convert no-answer to no_answer
                    campaign_id=workflow_run.campaign_id,
                    queued_run_id=workflow_run.queued_run_id,
                )

            # Update workflow run with appropriate tags
            call_tags = workflow_run.gathered_context.get("call_tags", [])
            call_tags.extend(["not_connected", f"twilio_{CallStatus.lower()}"])

            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                is_completed=True,
                gathered_context={
                    "call_tags": call_tags,
                },
            )

        return {"status": "success", "message": "Callback processed"}

    except Exception as e:
        logger.error(f"Error processing Twilio status callback: {e}")
        return {"status": "error", "message": str(e)}
