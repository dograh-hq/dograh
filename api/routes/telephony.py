"""
Telephony routes - handles all telephony-related endpoints.
Consolidated from split modules for easier maintenance.
"""

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Dict, Optional

from fastapi import (
    APIRouter,
    Depends,
    Form,
    Header,
    HTTPException,
    Request,
    WebSocket,
)
from loguru import logger
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.future import select
from starlette.responses import HTMLResponse
from starlette.websockets import WebSocketDisconnect

from api.db import db_client
from api.db.models import OrganizationConfigurationModel, UserModel
from api.db.workflow_client import WorkflowClient
from api.db.workflow_run_client import WorkflowRunClient
from api.enums import CallType, OrganizationConfigurationKey, WorkflowRunState
from api.errors.telephony_errors import TelephonyError
from api.services.auth.depends import get_user
from api.services.campaign.campaign_call_dispatcher import campaign_call_dispatcher
from api.services.campaign.campaign_event_publisher import get_campaign_event_publisher
from api.services.quota_service import check_dograh_quota, check_dograh_quota_by_user_id
from api.services.telephony.transfer_coordination import get_transfer_coordinator
from api.services.telephony.transfer_event_protocol import TransferContext
from api.services.telephony.factory import (
    get_all_telephony_providers,
    get_telephony_provider,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_helper import (
    generic_hangup_response,
    normalize_webhook_data,
    numbers_match,
    parse_webhook_request,
)
from pipecat.services.llm_service import FunctionCallParams
from pipecat.utils.run_context import set_current_run_id

router = APIRouter(prefix="/telephony")

# Module-level storage for webhook-driven function call completion
# Stores function call contexts that are waiting for webhook completion
pending_function_calls: Dict[str, tuple[FunctionCallParams, float]] = {}

# Note: Transfer contexts now stored in Redis via TransferCoordinator
# pending_transfers dictionary removed in favor of Redis-based coordination


class InitiateCallRequest(BaseModel):
    workflow_id: int
    workflow_run_id: int | None = None
    phone_number: str | None = None


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
            extra=data,
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
            "rejected": "busy",
        }

        return cls(
            call_id=data.get("uuid", ""),
            status=status_map.get(data.get("status", ""), data.get("status", "")),
            from_number=data.get("from"),
            to_number=data.get("to"),
            direction=data.get("direction"),
            duration=data.get("duration"),
            extra=data,
        )

    @classmethod
    def from_cloudonix_cdr(cls, data: dict):
        """Convert Cloudonix CDR to generic format"""
        # Map Cloudonix disposition to common format
        disposition_map = {
            "ANSWER": "completed",
            "BUSY": "busy",
            "CANCEL": "canceled",
            "FAILED": "failed",
            "CONGESTION": "failed",
            "NOANSWER": "no-answer",
        }

        disposition = data.get("disposition", "")
        status = disposition_map.get(disposition.upper(), disposition.lower())

        return cls(
            call_id=data.get("session").get("token"),
            status=status,
            from_number=data.get("from"),
            to_number=data.get("to"),
            duration=str(data.get("billsec") or data.get("duration") or 0),
            extra=data,
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

    # Check Dograh quota before initiating the call
    quota_result = await check_dograh_quota(user)
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    # Determine the workflow run mode based on provider type
    workflow_run_mode = provider.PROVIDER_NAME

    user_configuration = await db_client.get_user_configurations(user.id)

    phone_number = request.phone_number or user_configuration.test_phone_number

    if not phone_number:
        raise HTTPException(
            status_code=400,
            detail="Phone number must be provided in request or set in user "
            "configuration",
        )

    workflow_run_id = request.workflow_run_id

    if not workflow_run_id:
        numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
        workflow_run_name = f"WR-TEL-OUT-{numeric_suffix:08d}"
        workflow_run = await db_client.create_workflow_run(
            workflow_run_name,
            request.workflow_id,
            workflow_run_mode,
            user_id=user.id,
            call_type=CallType.OUTBOUND,
            initial_context={
                "phone_number": phone_number,
                "provider": provider.PROVIDER_NAME,
            },
        )
        workflow_run_id = workflow_run.id
    else:
        workflow_run = await db_client.get_workflow_run(workflow_run_id, user.id)
        if not workflow_run:
            raise HTTPException(status_code=400, detail="Workflow run not found")
        workflow_run_name = workflow_run.name

    # Construct webhook URL based on provider type
    backend_endpoint, _ = await get_backend_endpoints()

    webhook_endpoint = provider.WEBHOOK_ENDPOINT

    webhook_url = (
        f"{backend_endpoint}/api/v1/telephony/{webhook_endpoint}"
        f"?workflow_id={request.workflow_id}"
        f"&user_id={user.id}"
        f"&workflow_run_id={workflow_run_id}"
        f"&organization_id={user.selected_organization_id}"
    )

    keywords = {"workflow_id": request.workflow_id, "user_id": user.id}

    # Initiate call via provider
    result = await provider.initiate_call(
        to_number=phone_number,
        webhook_url=webhook_url,
        workflow_run_id=workflow_run_id,
        **keywords,
    )

    # Store provider type and any provider-specific metadata in workflow run context
    gathered_context = {
        "provider": provider.PROVIDER_NAME,
        **(result.provider_metadata or {}),
    }
    await db_client.update_workflow_run(
        run_id=workflow_run_id, gathered_context=gathered_context
    )

    return {"message": f"Call initiated successfully with run name {workflow_run_name}"}


async def _verify_organization_phone_number(
    phone_number: str,
    organization_id: int,
    to_country: str = None,
    from_country: str = None,
) -> bool:
    """
    Verify that a phone number belongs to the specified organization.

    Args:
        phone_number: The phone number to verify
        organization_id: The organization ID to check against
        to_country: ISO country code for the called number (e.g., "US", "IN")
        from_country: ISO country code for the caller (e.g., "IN", "GB")

    Returns:
        True if the phone number belongs to the organization, False otherwise
    """
    try:
        async with db_client.async_session() as session:
            result = await session.execute(
                select(OrganizationConfigurationModel).where(
                    OrganizationConfigurationModel.organization_id == organization_id,
                    OrganizationConfigurationModel.key
                    == OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
                )
            )

            config = result.scalars().first()

            if not config or not config.value:
                logger.warning(
                    f"No telephony configuration found for organization {organization_id}"
                )
                return False

            from_numbers = config.value.get("from_numbers", [])
            logger.debug(
                f"Organization {organization_id} has from_numbers: {from_numbers}"
            )

            for configured_number in from_numbers:
                if numbers_match(
                    phone_number, configured_number, to_country, from_country
                ):
                    logger.info(
                        f"Phone number {phone_number} verified for organization {organization_id} "
                        f"(matches {configured_number}, to_country={to_country}, from_country={from_country})"
                    )
                    return True

            logger.warning(
                f"Phone number {phone_number} not found in organization {organization_id} from_numbers: {from_numbers} "
                f"(to_country={to_country}, from_country={from_country})"
            )
            return False

    except Exception as e:
        logger.error(
            f"Error verifying phone number {phone_number} for organization {organization_id}: {e}"
        )
        return False


async def _detect_provider(webhook_data: dict, headers: dict):
    """Detect which telephony provider can handle this webhook"""
    provider_classes = await get_all_telephony_providers()

    for provider_class in provider_classes:
        if provider_class.can_handle_webhook(webhook_data, headers):
            return provider_class

    logger.warning(f"No provider found for webhook data: {webhook_data.keys()}")
    return None


async def _validate_inbound_request(
    workflow_id: int,
    provider_class,
    normalized_data,
    webhook_data: dict,
    webhook_body: str = "",
    x_twilio_signature: str = None,
    x_vobiz_signature: str = None,
    x_vobiz_timestamp: str = None,
    x_cx_apikey: str = None,
) -> tuple[bool, TelephonyError, dict, object]:
    """
    Validate all aspects of inbound request.
    Returns: (is_valid, error_type, workflow_context, provider_instance)
    """

    workflow = await db_client.get_workflow(workflow_id)
    if not workflow:
        return False, TelephonyError.WORKFLOW_NOT_FOUND, {}, None

    organization_id = workflow.organization_id
    user_id = workflow.user_id
    provider = normalized_data.provider

    # Validate provider and account_id
    validation_result = await _validate_organization_provider_config(
        organization_id, provider_class, normalized_data.account_id
    )
    if validation_result != TelephonyError.VALID:
        return False, validation_result, {}, None

    # Verify phone number belongs to organization
    is_valid = await _verify_organization_phone_number(
        normalized_data.to_number,
        organization_id,
        normalized_data.to_country,
        normalized_data.from_country,
    )
    if not is_valid:
        return False, TelephonyError.PHONE_NUMBER_NOT_CONFIGURED, {}, None

    # Verify webhook signature/API key if provided
    provider_instance = None
    if x_twilio_signature or x_vobiz_signature or x_cx_apikey:
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = f"{backend_endpoint}/api/v1/telephony/inbound/{workflow_id}"

        # Get the real telephony provider with actual credentials for signature verification
        provider_instance = await get_telephony_provider(organization_id)

        if provider_class.PROVIDER_NAME == "twilio" and x_twilio_signature:
            logger.info(f"Verifying Twilio signature for URL: {webhook_url}")
            signature_valid = await provider_instance.verify_inbound_signature(
                webhook_url, webhook_data, x_twilio_signature
            )
        elif provider_class.PROVIDER_NAME == "vobiz" and x_vobiz_signature:
            logger.info(f"Verifying Vobiz signature for URL: {webhook_url}")
            signature_valid = await provider_instance.verify_inbound_signature(
                webhook_url,
                webhook_data,
                x_vobiz_signature,
                x_vobiz_timestamp,
                webhook_body,
            )
        elif provider_class.PROVIDER_NAME == "cloudonix" and x_cx_apikey:
            logger.info(f"Verifying Cloudonix API key for URL: {webhook_url}")
            signature_valid = await provider_instance.verify_inbound_signature(
                webhook_url, webhook_data, x_cx_apikey
            )
        else:
            logger.warning(
                f"No signature/API key validation for provider {provider_class.PROVIDER_NAME}"
            )
            signature_valid = True

        logger.info(f"Signature/API key validation result: {signature_valid}")
        if not signature_valid:
            return (
                False,
                TelephonyError.SIGNATURE_VALIDATION_FAILED,
                {},
                provider_instance,
            )

    # Return success with workflow context
    workflow_context = {
        "workflow": workflow,
        "organization_id": organization_id,
        "user_id": user_id,
        "provider": provider,
    }
    return (
        True,
        "",
        workflow_context,
        provider_instance,
    )  # TODO: do we still need instance in the client code


async def _create_inbound_workflow_run(
    workflow_id: int, user_id: int, provider: str, normalized_data, data_source: str
) -> int:
    """Create workflow run for inbound call and return run ID"""
    call_id = normalized_data.call_id
    numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
    workflow_run_name = f"WR-TEL-IN-{numeric_suffix:08d}"

    workflow_run = await db_client.create_workflow_run(
        workflow_run_name,
        workflow_id,
        provider,  # Use detected provider as mode
        user_id=user_id,
        call_type=CallType.INBOUND,
        initial_context={
            "caller_number": normalized_data.from_number,
            "called_number": normalized_data.to_number,
            "direction": "inbound",
            "call_id": call_id,
            "account_id": normalized_data.account_id,
            "provider": provider,
            "data_source": data_source,
            "from_country": normalized_data.from_country,
            "to_country": normalized_data.to_country,
            "raw_webhook_data": normalized_data.raw_data,
        },
    )

    logger.info(
        f"Created inbound workflow run {workflow_run.id} for {provider} call {call_id}"
    )
    return workflow_run.id


async def _validate_organization_provider_config(
    organization_id: int, provider_class, account_id: str
) -> TelephonyError:
    """Validate provider and account_id, returning specific error type"""
    if not account_id:
        logger.warning(
            f"No account_id provided for provider {provider_class.PROVIDER_NAME}"
        )
        return TelephonyError.ACCOUNT_VALIDATION_FAILED

    try:
        config = await db_client.get_configuration(
            organization_id,
            OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
        )

        if not config or not config.value:
            logger.warning(
                f"No telephony configuration found for organization {organization_id}"
            )
            return TelephonyError.ACCOUNT_VALIDATION_FAILED

        stored_provider = config.value.get("provider")
        if stored_provider != provider_class.PROVIDER_NAME:
            logger.warning(
                f"Provider mismatch: webhook={provider_class.PROVIDER_NAME}, config={stored_provider}"
            )
            return TelephonyError.PROVIDER_MISMATCH

        # Use provider-specific validation
        is_valid = provider_class.validate_account_id(config.value, account_id)
        if not is_valid:
            logger.warning(
                f"Account validation failed for {provider_class.PROVIDER_NAME}: webhook={account_id}"
            )
            return TelephonyError.ACCOUNT_VALIDATION_FAILED

        return TelephonyError.VALID

    except Exception as e:
        logger.error(f"Exception during account validation: {e}")
        return TelephonyError.ACCOUNT_VALIDATION_FAILED


@router.post("/twiml", include_in_schema=False)
async def handle_twiml_webhook(
    workflow_id: int, user_id: int, workflow_run_id: int, organization_id: int, CallSid: str = Form(...)
):
    """
    Handle initial webhook from telephony provider.
    Returns provider-specific response (e.g., TwiML for Twilio).
    """
    logger.info(f"[TWIML-DEBUG] CallSid received: {CallSid}")

    provider = await get_telephony_provider(organization_id)

    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )

    return HTMLResponse(content=response_content, media_type="application/xml")


@router.post("/transfer-twiml/{conference_name}", include_in_schema=False)
async def transfer_twiml(conference_name: str):
    """
    TwiML endpoint that puts the caller into a conference.
    Called by Twilio when we redirect the call after closing the WebSocket stream.
    """
    logger.info(f"[TRANSFER-TWIML] Generating conference TwiML for: {conference_name}")
    
    twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Connecting you now.</Say>
    <Dial>
        <Conference endConferenceOnExit="false" startConferenceOnEnter="true">{conference_name}</Conference>
    </Dial>
</Response>"""
    
    logger.info(f"[TRANSFER-TWIML] Generated TwiML: {twiml_content}")
    return HTMLResponse(content=twiml_content, media_type="application/xml")


@router.get("/ncco", include_in_schema=False)
async def handle_ncco_webhook(
    workflow_id: int,
    user_id: int,
    workflow_run_id: int,
    organization_id: Optional[int] = None,
):
    """Handle NCCO (Nexmo Call Control Objects) webhook for Vonage.

    Returns JSON response instead of XML like TwiML.
    """

    provider = await get_telephony_provider(organization_id or user_id)

    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )

    return json.loads(response_content)


@router.websocket("/ws/{workflow_id}/{user_id}/{workflow_run_id}")
async def websocket_endpoint(
    websocket: WebSocket, workflow_id: int, user_id: int, workflow_run_id: int
):
    """WebSocket endpoint for real-time call handling - routes to provider-specific handlers."""
    await websocket.accept()

    try:
        # Set the run context
        set_current_run_id(workflow_run_id)

        # Get workflow run to determine provider type
        workflow_run = await db_client.get_workflow_run(workflow_run_id)
        if not workflow_run:
            logger.error(f"Workflow run {workflow_run_id} not found")
            await websocket.close(code=4404, reason="Workflow run not found")
            return

        # Get workflow for organization info
        workflow = await db_client.get_workflow(workflow_id)
        if not workflow:
            logger.error(f"Workflow {workflow_id} not found")
            await websocket.close(code=4404, reason="Workflow not found")
            return

        # Check workflow run state - only allow 'initialized' state
        if workflow_run.state != WorkflowRunState.INITIALIZED.value:
            logger.warning(
                f"Workflow run {workflow_run_id} not in initialized state: {workflow_run.state}"
            )
            await websocket.close(
                code=4409, reason="Workflow run not available for connection"
            )
            return

        # Extract provider type from workflow run context
        provider_type = None
        logger.info(
            f"Workflow run {workflow_run_id} gathered_context: {workflow_run.gathered_context}"
        )
        logger.info(f"Workflow run {workflow_run_id} mode: {workflow_run.mode}")

        if workflow_run.initial_context:
            provider_type = workflow_run.initial_context.get("provider")
            logger.info(f"Extracted provider_type: {provider_type}")

        if not provider_type:
            logger.error(
                f"No provider type found in workflow run {workflow_run_id}. "
                f"gathered_context: {workflow_run.gathered_context}, mode: {workflow_run.mode}"
            )
            await websocket.close(code=4400, reason="Provider type not found")
            return

        logger.info(
            f"WebSocket connected for {provider_type} provider, workflow_run {workflow_run_id}"
        )

        # Get the telephony provider instance
        provider = await get_telephony_provider(workflow.organization_id)

        # Verify the provider matches what was stored
        if provider.PROVIDER_NAME != provider_type:
            logger.error(
                f"Provider mismatch: expected {provider_type}, got {provider.PROVIDER_NAME}"
            )
            await websocket.close(code=4400, reason="Provider mismatch")
            return

        # Set workflow run state to 'running' before starting the pipeline
        await db_client.update_workflow_run(
            run_id=workflow_run_id, state=WorkflowRunState.RUNNING.value
        )

        logger.info(
            f"[run {workflow_run_id}] Set workflow run state to 'running' for {provider_type} provider"
        )

        # Delegate to provider-specific handler
        await provider.handle_websocket(
            websocket, workflow_id, user_id, workflow_run_id
        )

    except WebSocketDisconnect as e:
        logger.info(f"WebSocket disconnected: code={e.code}, reason={e.reason}")
    except Exception as e:
        logger.error(f"Error in WebSocket connection: {e}")
        try:
            await websocket.close(1011, "Internal server error")
        except RuntimeError:
            # WebSocket already closed, ignore
            pass


@router.post("/twilio/status-callback/{workflow_run_id}")
async def handle_twilio_status_callback(
    workflow_run_id: int,
    request: Request,
    x_webhook_signature: Optional[str] = Header(None),
):
    """Handle Twilio-specific status callbacks."""
    set_current_run_id(workflow_run_id)

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

    # Get workflow and provider
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"Workflow {workflow_run.workflow_id} not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider(workflow.organization_id)

    if x_webhook_signature:
        backend_endpoint, _ = await get_backend_endpoints()
        full_url = f"{backend_endpoint}/api/v1/telephony/twilio/status-callback/{workflow_run_id}"

        is_valid = await provider.verify_webhook_signature(
            full_url, callback_data, x_webhook_signature
        )

        if not is_valid:
            logger.warning(
                f"Invalid webhook signature for workflow run {workflow_run_id}"
            )
            return {"status": "error", "reason": "invalid_signature"}

    # Parse the callback data into generic format
    parsed_data = provider.parse_status_callback(callback_data)

    # Create StatusCallbackRequest from parsed data
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    return {"status": "success"}


async def _process_status_update(workflow_run_id: int, status: StatusCallbackRequest):
    """Process status updates from telephony providers."""

    # Fetch fresh workflow_run to ensure we have the latest state
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found in status update"
        )
        return

    # Log the status callback
    telephony_callback_logs = workflow_run.logs.get("telephony_status_callbacks", [])
    telephony_callback_log = {
        "status": status.status,
        "timestamp": datetime.now(UTC).isoformat(),
        "call_id": status.call_id,
        "duration": status.duration,
        **status.extra,  # Include provider-specific data
    }
    telephony_callback_logs.append(telephony_callback_log)

    # Update workflow run logs
    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        logs={"telephony_status_callbacks": telephony_callback_logs},
    )

    # The workflow run state is already marked as completed from either status-update
    # callbacks or CDR update callbacks. Lets skip processing.
    if workflow_run.state == WorkflowRunState.COMPLETED.value:
        return

    # Handle call completion - make these updates idempotent - i.e
    # they should handle multiple API calls (one due to status update,
    # and other due to CDR updates.)
    if status.status == "completed":
        logger.info(
            f"[run {workflow_run_id}] Call completed with duration: {status.duration}s"
        )

        # Release concurrent slot if this was a campaign call
        if workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)

        # Mark workflow run as completed
        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
        )

    elif status.status in ["failed", "busy", "no-answer", "canceled", "error"]:
        logger.warning(
            f"[run {workflow_run_id}] Call failed with status: {status.status}"
        )

        # Release concurrent slot for terminal statuses if this was a campaign call
        if workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)

        # Check if retry is needed for campaign calls (busy/no-answer)
        if status.status in ["busy", "no-answer"] and workflow_run.campaign_id:
            publisher = await get_campaign_event_publisher()
            await publisher.publish_retry_needed(
                workflow_run_id=workflow_run_id,
                reason=status.status.replace(
                    "-", "_"
                ),  # Convert no-answer to no_answer
                campaign_id=workflow_run.campaign_id,
                queued_run_id=workflow_run.queued_run_id,
            )

        # Mark workflow run as completed with failure tags
        call_tags = (
            workflow_run.gathered_context.get("call_tags", [])
            if workflow_run.gathered_context
            else []
        )
        call_tags.extend(["not_connected", f"telephony_{status.status.lower()}"])

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
            gathered_context={"call_tags": call_tags},
        )


@router.post("/vonage/events/{workflow_run_id}")
async def handle_vonage_events(
    request: Request,
    workflow_run_id: int,
):
    """Handle Vonage-specific event webhooks.

    Vonage sends all call events to a single endpoint.
    Events include: started, ringing, answered, complete, failed, etc.
    """
    set_current_run_id(workflow_run_id)
    # Parse the event data
    event_data = await request.json()
    logger.info(f"[run {workflow_run_id}] Received Vonage event: {event_data}")

    # Get workflow run for processing
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.error(f"[run {workflow_run_id}] Workflow run not found")
        return {"status": "error", "message": "Workflow run not found"}

    # For a completed call that includes cost info, capture it immediately
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
                        cost_info["vonage_webhook_duration"] = int(
                            event_data["duration"]
                        )

                    await db_client.update_workflow_run(
                        run_id=workflow_run_id, cost_info=cost_info
                    )
                    logger.info(
                        f"[run {workflow_run_id}] Captured Vonage cost info from webhook"
                    )
            except Exception as e:
                logger.error(
                    f"[run {workflow_run_id}] Failed to capture Vonage cost from webhook: {e}"
                )

    # Get workflow and provider
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.error(f"[run {workflow_run_id}] Workflow not found")
        return {"status": "error", "message": "Workflow not found"}

    provider = await get_telephony_provider(workflow.organization_id)

    # Parse the event data into generic format
    parsed_data = provider.parse_status_callback(event_data)

    # Create StatusCallbackRequest from parsed data
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    # Return 204 No Content as expected by Vonage
    return {"status": "ok"}


@router.post("/vobiz-xml", include_in_schema=False)
async def handle_vobiz_xml_webhook(
    workflow_id: int, user_id: int, workflow_run_id: int, organization_id: int
):
    """
    Handle initial webhook from Vobiz when call is answered.
    Returns Vobiz XML response with Stream element.

    Vobiz uses Plivo-compatible XML format similar to Twilio's TwiML.
    """
    set_current_run_id(workflow_run_id)
    logger.info(
        f"[run {workflow_run_id}] Vobiz XML webhook called - "
        f"workflow_id={workflow_id}, user_id={user_id}, org_id={organization_id}"
    )

    provider = await get_telephony_provider(organization_id)

    logger.debug(f"[run {workflow_run_id}] Using provider: {provider.PROVIDER_NAME}")

    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )

    logger.debug(
        f"[run {workflow_run_id}] Vobiz XML response generated:\n{response_content}"
    )

    return HTMLResponse(content=response_content, media_type="application/xml")


@router.post("/vobiz/hangup-callback/{workflow_run_id}")
async def handle_vobiz_hangup_callback(
    workflow_run_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz hangup callback (sent when call ends).

    Vobiz sends callbacks to hangup_url when the call terminates.
    This includes call duration, status, and billing information.
    """
    set_current_run_id(workflow_run_id)

    # Logging all headers and body to understand what Vobiz actually sends
    all_headers = dict(request.headers)
    logger.info(
        f"[run {workflow_run_id}] Vobiz hangup callback - Headers: {json.dumps(all_headers)}"
    )

    # Parse the callback data (Vobiz sends form data or JSON)
    form_data = await request.form()
    callback_data = dict(form_data)

    # TODO: Remove this debug logging after Vobiz team clarifies webhook authentication
    logger.info(
        f"[run {workflow_run_id}] Vobiz hangup callback - Body: {json.dumps(callback_data)}"
    )
    logger.info(
        f"[run {workflow_run_id}] Received Vobiz hangup callback {json.dumps(callback_data)}"
    )

    # Verify signature if provided
    if x_vobiz_signature:
        # We need the workflow run to get organization for provider credentials
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            logger.warning(
                f"[run {workflow_run_id}] Workflow run not found for signature verification"
            )
            return {"status": "error", "reason": "workflow_run_not_found"}

        workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
        if not workflow:
            logger.warning(
                f"[run {workflow_run_id}] Workflow not found for signature verification"
            )
            return {"status": "error", "reason": "workflow_not_found"}

        provider = await get_telephony_provider(workflow.organization_id)

        # Get raw body for signature verification
        raw_body = await request.body()
        webhook_body = raw_body.decode("utf-8")

        # Verify signature
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = f"{backend_endpoint}/api/v1/telephony/vobiz/hangup-callback/{workflow_run_id}"

        is_valid = await provider.verify_webhook_signature(
            webhook_url,
            callback_data,
            x_vobiz_signature,
            x_vobiz_timestamp,
            webhook_body,
        )

        if not is_valid:
            logger.warning(
                f"[run {workflow_run_id}] Invalid Vobiz hangup callback signature"
            )
            return {"status": "error", "reason": "invalid_signature"}

        logger.info(f"[run {workflow_run_id}] Vobiz hangup callback signature verified")
    else:
        # Get workflow run for processing (signature verification already got it if needed)
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for Vobiz hangup callback"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    # Get workflow and provider
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"[run {workflow_run_id}] Workflow not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider(workflow.organization_id)

    logger.debug(
        f"[run {workflow_run_id}] Processing Vobiz hangup with provider: {provider.PROVIDER_NAME}"
    )

    # Parse the callback data into generic format
    parsed_data = provider.parse_status_callback(callback_data)

    logger.debug(
        f"[run {workflow_run_id}] Parsed Vobiz callback data: {json.dumps(parsed_data)}"
    )

    # Create StatusCallbackRequest from parsed data
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    logger.info(f"[run {workflow_run_id}] Vobiz hangup callback processed successfully")

    return {"status": "success"}


@router.post("/vobiz/ring-callback/{workflow_run_id}")
async def handle_vobiz_ring_callback(
    workflow_run_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz ring callback (sent when call starts ringing).

    Vobiz can send callbacks to ring_url when the call starts ringing.
    This is optional and used for tracking ringing status.
    """
    set_current_run_id(workflow_run_id)

    # Logging all headers and body to understand what Vobiz actually sends
    all_headers = dict(request.headers)
    logger.info(
        f"[run {workflow_run_id}] Vobiz ring callback - Headers: {json.dumps(all_headers)}"
    )

    # Parse the callback data
    form_data = await request.form()
    callback_data = dict(form_data)

    # TODO: Remove this debug logging after Vobiz team clarifies webhook authentication
    logger.info(
        f"[run {workflow_run_id}] Vobiz ring callback - Body: {json.dumps(callback_data)}"
    )

    logger.info(
        f"[run {workflow_run_id}] Received Vobiz ring callback {json.dumps(callback_data)}"
    )

    # Verify signature if provided
    if x_vobiz_signature:
        # We need the workflow run to get organization for provider credentials
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            logger.warning(
                f"[run {workflow_run_id}] Workflow run not found for signature verification"
            )
            return {"status": "error", "reason": "workflow_run_not_found"}

        workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
        if not workflow:
            logger.warning(
                f"[run {workflow_run_id}] Workflow not found for signature verification"
            )
            return {"status": "error", "reason": "workflow_not_found"}

        provider = await get_telephony_provider(workflow.organization_id)

        # Get raw body for signature verification
        raw_body = await request.body()
        webhook_body = raw_body.decode("utf-8")

        # Verify signature
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = (
            f"{backend_endpoint}/api/v1/telephony/vobiz/ring-callback/{workflow_run_id}"
        )

        is_valid = await provider.verify_webhook_signature(
            webhook_url,
            callback_data,
            x_vobiz_signature,
            x_vobiz_timestamp,
            webhook_body,
        )

        if not is_valid:
            logger.warning(
                f"[run {workflow_run_id}] Invalid Vobiz ring callback signature"
            )
            return {"status": "error", "reason": "invalid_signature"}

        logger.info(f"[run {workflow_run_id}] Vobiz ring callback signature verified")
    else:
        # Get workflow run for processing (signature verification already got it if needed)
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for Vobiz ring callback"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    # Log the ringing event
    telephony_callback_logs = workflow_run.logs.get("telephony_status_callbacks", [])
    ring_log = {
        "status": "ringing",
        "timestamp": datetime.now(UTC).isoformat(),
        "call_id": callback_data.get("call_uuid", callback_data.get("CallUUID", "")),
        "event_type": "ring",
        "raw_data": callback_data,
    }
    telephony_callback_logs.append(ring_log)

    # Update workflow run logs
    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        logs={"telephony_status_callbacks": telephony_callback_logs},
    )

    logger.info(f"[run {workflow_run_id}] Vobiz ring callback logged")

    return {"status": "success"}


@router.post("/cloudonix/status-callback/{workflow_run_id}")
async def handle_cloudonix_status_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle Cloudonix-specific status callbacks.

    Cloudonix sends call status updates to the callback URL specified during call initiation.
    """
    set_current_run_id(workflow_run_id)
    # Parse callback data - determine if JSON or form data
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        callback_data = await request.json()
    else:
        # Assume form data (like Twilio)
        form_data = await request.form()
        callback_data = dict(form_data)

    logger.info(
        f"[run {workflow_run_id}] Received Cloudonix status callback: {json.dumps(callback_data)}"
    )

    # Get workflow run to find organization
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(f"Workflow run {workflow_run_id} not found for status callback")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    # Get workflow and provider
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"Workflow {workflow_run.workflow_id} not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider(workflow.organization_id)

    # Parse the callback data into generic format
    parsed_data = provider.parse_status_callback(callback_data)

    # Create StatusCallbackRequest from parsed data
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    return {"status": "success"}


@router.post("/vobiz/hangup-callback/workflow/{workflow_id}")
async def handle_vobiz_hangup_callback_by_workflow(
    workflow_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz hangup callback with workflow_id - finds workflow run by call_id."""

    all_headers = dict(request.headers)
    logger.info(
        f"[workflow {workflow_id}] Vobiz hangup callback - Headers: {json.dumps(all_headers)}"
    )

    try:
        callback_data, _ = await parse_webhook_request(request)
    except ValueError:
        callback_data = {}

    call_uuid = callback_data.get("CallUUID") or callback_data.get("call_uuid")
    logger.info(
        f"[workflow {workflow_id}] Received Vobiz hangup callback for call {call_uuid}: {json.dumps(callback_data)}"
    )

    if not call_uuid:
        logger.warning(
            f"[workflow {workflow_id}] No call_uuid found in Vobiz hangup callback"
        )
        return {"status": "error", "message": "No call_uuid found"}

    workflow_client = WorkflowClient()
    workflow = await workflow_client.get_workflow_by_id(workflow_id)
    if not workflow:
        logger.warning(f"[workflow {workflow_id}] Workflow not found")
        return {"status": "error", "message": "workflow_not_found"}

    provider = await get_telephony_provider(workflow.organization_id)

    if x_vobiz_signature:
        raw_body = await request.body()
        webhook_body = raw_body.decode("utf-8")
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = f"{backend_endpoint}/api/v1/telephony/vobiz/hangup-callback/workflow/{workflow_id}"

        is_valid = await provider.verify_webhook_signature(
            webhook_url,
            callback_data,
            x_vobiz_signature,
            x_vobiz_timestamp,
            webhook_body,
        )

        if not is_valid:
            logger.warning(
                f"[workflow {workflow_id}] Invalid Vobiz hangup callback signature"
            )
            return {"status": "error", "message": "invalid_signature"}

        logger.info(
            f"[workflow {workflow_id}] Vobiz hangup callback signature verified"
        )

    try:
        db_client = WorkflowRunClient()
        async with db_client.async_session() as session:
            # Fetch workflow run with matching call_id in initial_context
            query = text("""
                SELECT id FROM workflow_runs 
                WHERE workflow_id = :workflow_id 
                AND CAST(initial_context AS jsonb) @> CAST(:call_id_json AS jsonb)
                ORDER BY created_at DESC 
                LIMIT 1
            """)

            result = await session.execute(
                query,
                {
                    "workflow_id": workflow_id,
                    "call_id_json": json.dumps({"call_id": call_uuid}),
                },
            )
            workflow_run_row = result.fetchone()

            if not workflow_run_row:
                logger.warning(
                    f"[workflow {workflow_id}] No workflow run found for call {call_uuid}"
                )
                return {"status": "ignored", "reason": "workflow_run_not_found"}

            workflow_run_id = workflow_run_row[0]
            set_current_run_id(workflow_run_id)
            logger.info(
                f"[workflow {workflow_id}] Found workflow run {workflow_run_id} for call {call_uuid}"
            )

    except Exception as e:
        logger.error(
            f"[workflow {workflow_id}] Error finding workflow run for call {call_uuid}: {e}"
        )
        return {"status": "error", "message": str(e)}

    try:
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            logger.warning(f"[run {workflow_run_id}] Workflow run not found")
            return {"status": "ignored", "reason": "workflow_run_not_found"}

        parsed_data = provider.parse_status_callback(callback_data)

        status = StatusCallbackRequest(
            call_id=parsed_data["call_id"],
            status=parsed_data["status"],
            from_number=parsed_data.get("from_number"),
            to_number=parsed_data.get("to_number"),
            direction=parsed_data.get("direction"),
            duration=parsed_data.get("duration"),
            extra=parsed_data.get("extra", {}),
        )

        await _process_status_update(workflow_run_id, status)

        logger.info(
            f"[run {workflow_run_id}] Vobiz hangup callback processed successfully"
        )
        return {"status": "success"}

    except Exception as e:
        logger.error(
            f"[run {workflow_run_id}] Error processing Vobiz hangup callback: {e}"
        )
        return {"status": "error", "message": str(e)}


@router.post("/inbound/{workflow_id}")
async def handle_inbound_telephony(
    workflow_id: int,
    request: Request,
    x_twilio_signature: Optional[str] = Header(None),
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
    x_cx_apikey: Optional[str] = Header(None),
):
    """Handle inbound telephony calls from any supported provider with common processing"""
    logger.info(f"Inbound call received for workflow_id: {workflow_id}")

    try:
        webhook_data, data_source = await parse_webhook_request(request)
        logger.info(
            f"Inbound call data with data source: {data_source} and data :{dict(webhook_data)}"
        )
        headers = dict(request.headers)

        # Detect provider and normalize data
        provider_class = await _detect_provider(webhook_data, headers)
        if not provider_class:
            logger.error("Unable to detect provider for webhook")
            return generic_hangup_response()

        normalized_data = normalize_webhook_data(provider_class, webhook_data)

        logger.info(
            f"Inbound call - Provider: {normalized_data.provider}, Data source: {data_source}"
        )
        logger.info(f"Normalized data: {normalized_data}")

        # Validate inbound direction
        if normalized_data.direction != "inbound":
            logger.warning(f"Non-inbound call received: {normalized_data.direction}")
            return generic_hangup_response()

        logger.info(f"Inbound call headers: {dict(request.headers)}")
        logger.info(f"Twilio signature header: {x_twilio_signature}")
        logger.info(f"Vobiz signature header: {x_vobiz_signature}")
        logger.info(f"Vobiz timestamp header: {x_vobiz_timestamp}")

        webhook_body = ""
        if provider_class.PROVIDER_NAME == "vobiz":
            webhook_body = data_source
            logger.info(f"Vobiz inbound call - Body: {json.dumps(webhook_data)}")

        (
            is_valid,
            error_type,
            workflow_context,
            provider_instance,
        ) = await _validate_inbound_request(
            workflow_id,
            provider_class,
            normalized_data,
            webhook_data,
            webhook_body,
            x_twilio_signature,
            x_vobiz_signature,
            x_vobiz_timestamp,
            x_cx_apikey,
        )

        if not is_valid:
            logger.error(f"Request validation failed: {error_type}")
            return provider_class.generate_validation_error_response(error_type)

        # Check quota before processing
        user_id = workflow_context["user_id"]
        quota_result = await check_dograh_quota_by_user_id(user_id)
        if not quota_result.has_quota:
            logger.warning(
                f"User {user_id} has exceeded quota for inbound calls: {quota_result.error_message}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.QUOTA_EXCEEDED
            )

        # Create workflow run
        workflow_run_id = await _create_inbound_workflow_run(
            workflow_id,
            workflow_context["user_id"],
            workflow_context["provider"],
            normalized_data,
            data_source,
        )

        # Generate response URLs
        _, wss_backend_endpoint = await get_backend_endpoints()
        websocket_url = f"{wss_backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{workflow_context['user_id']}/{workflow_run_id}"
        response = await provider_class.generate_inbound_response(
            websocket_url, workflow_run_id
        )

        logger.info(
            f"Generated {normalized_data.provider} response for call {normalized_data.call_id}"
        )
        logger.info(f"response is {response}")
        return response

    except ValueError as e:
        logger.error(f"Request parsing error: {e}")
        return generic_hangup_response()
    except Exception as e:
        logger.error(f"Error processing inbound call: {e}")
        return generic_hangup_response()


@router.post("/inbound/fallback")
async def handle_inbound_fallback(request: Request):
    """Fallback endpoint that returns audio message when calls cannot be processed."""

    webhook_data, _ = await parse_webhook_request(request)
    headers = dict(request.headers)

    # Detect provider
    provider_class = await _detect_provider(webhook_data, headers)

    if provider_class:
        # Use provider-specific error response
        call_id = (
            webhook_data.get("CallSid")
            or webhook_data.get("CallUUID")
            or webhook_data.get("call_uuid")
        )
        logger.info(
            f"[fallback] Received {provider_class.PROVIDER_NAME} callback for call {call_id}: {json.dumps(webhook_data)}"
        )

        return provider_class.generate_error_response(
            "SYSTEM_UNAVAILABLE",
            "Our system is temporarily unavailable. Please try again later.",
        )
    else:
        # Unknown provider - return generic XML
        logger.info(
            f"[fallback] Received unknown provider callback: {json.dumps(webhook_data)} and request headers: {json.dumps(headers)}"
        )

        return generic_hangup_response()
@router.post("/cloudonix/cdr")
async def handle_cloudonix_cdr(request: Request):
    """Handle Cloudonix CDR (Call Detail Record) webhooks.

    Cloudonix sends CDR records when calls complete. The CDR contains:
    - domain: Used to identify the organization
    - call_id: Used to find the workflow run
    - disposition: Call termination status (ANSWER, BUSY, CANCEL, FAILED, CONGESTION, NOANSWER)
    - duration/billsec: Call duration information
    """
    try:
        cdr_data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse Cloudonix CDR JSON: {e}")
        return {"status": "error", "message": "Invalid JSON payload"}

    # Extract domain to find organization
    domain = cdr_data.get("domain")
    if not domain:
        logger.warning("Cloudonix CDR missing domain field")
        return {"status": "error", "message": "Missing domain field"}

    # Extract call_id to find workflow run
    call_id = cdr_data.get("session").get("token")
    logger.info(f"Cloudonix CDR data for call id {call_id} - {cdr_data}")
    if not call_id:
        logger.warning("Cloudonix CDR missing call_id field")
        return {"status": "error", "message": "Missing call_id field"}

    # Find workflow run by call_id in gathered_context
    workflow_run = await db_client.get_workflow_run_by_call_id(call_id)
    if not workflow_run:
        logger.warning(f"No workflow run found for Cloudonix call_id: {call_id}")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow_run_id = workflow_run.id
    set_current_run_id(workflow_run_id)
    logger.info(f"[run {workflow_run_id}] Processing Cloudonix CDR for call {call_id}")

    # Convert CDR to status update using StatusCallbackRequest
    status_update = StatusCallbackRequest.from_cloudonix_cdr(cdr_data)

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    logger.info(
        f"[run {workflow_run_id}] Cloudonix CDR processed successfully - "
        f"disposition: {cdr_data.get('disposition')}, status: {status_update.status}"
    )


    return {"status": "success"}


class CallTransferRequest(BaseModel):
    """Request model for call transfer"""
    target_phone_number: Optional[str] = None
    phone_number: Optional[str] = None  # Alternative field name
    number: Optional[str] = None  # Another alternative
    current_call_sid: Optional[str] = None
    
    def get_target_number(self) -> str:
        """Get the target phone number from any of the possible fields"""
        return self.target_phone_number or self.phone_number or self.number or ""


class TransferCallRequest(BaseModel):
    """Request model for initiating call transfer using webhook-driven completion"""
    destination: str  # E.164 format phone number (required)
    organization_id: int  # Organization ID for provider configuration
    timeout: Optional[int] = 20  # seconds to wait for answer
    tool_call_id: Optional[str] = None  # will generate if not provided
    tool_uuid: Optional[str] = None  # tool UUID for tracing and validation
    original_call_sid: Optional[str] = None  # original caller's call SID
    caller_number: Optional[str] = None  # original caller's phone number

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, destination: str) -> str:
        """Validate destination is in E.164 format."""
        import re
        if not destination or not destination.strip():
            raise ValueError("Destination phone number is required")
        
        e164_pattern = r"^\+[1-9]\d{1,14}$"
        if not re.match(e164_pattern, destination.strip()):
            raise ValueError(f"Invalid phone number format: {destination}. Must be E.164 format (e.g., +1234567890)")
        
        return destination.strip()




@router.post("/call-transfer")
async def initiate_call_transfer(request: TransferCallRequest):
    """Initiate call transfer without blocking the pipeline"""
    import aiohttp
    
    logger.info(f"Received call transfer request: {request}")
    # Generate tool_call_id if not provided
    if not request.tool_call_id:
        request.tool_call_id = f"transfer_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    
    # Log tool details for tracing
    logger.info(f"Starting non-blocking call transfer to {request.destination} with tool_call_id: {request.tool_call_id}, tool_uuid: {request.tool_uuid}")
    
    # TODO: Add tool UUID validation here if needed
    # For example: Validate that the tool UUID corresponds to a valid transfer call tool
    # and that the destination matches the tool's configured destination pattern
    
    try:
        # Get provider that supports transfers (validates Twilio-only requirement)
        from api.services.telephony.factory import get_transfer_provider
        
        try:
            provider = await get_transfer_provider(request.organization_id)
        except ValueError as e:
            # Provider doesn't support transfers or organization not configured
            logger.error(f"Transfer provider validation failed: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Call transfer not supported: {str(e)}"
            )
        
        # Validate configuration before attempting transfer
        if not provider.validate_config():
            logger.error(f"Provider {provider.PROVIDER_NAME} configuration is invalid")
            raise HTTPException(
                status_code=500,
                detail=f"Telephony provider '{provider.PROVIDER_NAME}' is not properly configured for transfers"
            )
        
        # Initiate transfer call via provider
        logger.info(f"Initiating transfer call via {provider.PROVIDER_NAME} provider")
        try:
            transfer_result = await provider.transfer_call(
                destination=request.destination,
                tool_call_id=request.tool_call_id,
                timeout=request.timeout
            )
        except NotImplementedError as e:
            # This shouldn't happen due to get_transfer_provider validation, but safety check
            logger.error(f"Provider {provider.PROVIDER_NAME} doesn't support transfers: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{provider.PROVIDER_NAME}' does not support call transfers"
            )
        except Exception as e:
            # Provider API call failed
            logger.error(f"Provider transfer call failed: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Transfer call failed: {str(e)}"
            )
        
        call_sid = transfer_result.get("call_sid")
        logger.info(f"Transfer call initiated successfully: {call_sid}")
        logger.info(f"Transfer result: {transfer_result}")
        
        # Store the transfer context in Redis for webhook completion
        transfer_coordinator = await get_transfer_coordinator()
        transfer_context = TransferContext(
            tool_call_id=request.tool_call_id,
            call_sid=call_sid,
            target_number=request.destination,
            tool_uuid=request.tool_uuid,
            original_call_sid=request.original_call_sid,
            caller_number=request.caller_number,
            initiated_at=time.time(),
            workflow_run_id=0  # TODO: Add workflow_run_id to request if needed
        )
        await transfer_coordinator.store_transfer_context(transfer_context)
        
        # Return immediately without blocking
        return {
            "status": "transfer_initiated", 
            "call_id": call_sid,
            "message": f"Calling {request.destination}...",
            "tool_call_id": request.tool_call_id,
            "provider": provider.PROVIDER_NAME
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (already properly formatted)
        raise
    except Exception as e:
        # Catch any other unexpected errors
        logger.error(f"Unexpected error during transfer call: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error during transfer: {str(e)}"
        )


@router.post("/transfer-call-handler/{tool_call_id}")
async def handle_transfer_call_answered(tool_call_id: str, request: Request):
    """Handle when target answers the transfer call"""
    logger.info(f"Transfer call answered for tool_call_id: {tool_call_id}")
    
    form_data = await request.form()
    data = dict(form_data)
    call_sid = data.get("CallSid", "")
    
    # Get transfer context from Redis
    transfer_coordinator = await get_transfer_coordinator()
    transfer_context = await transfer_coordinator.get_transfer_context(tool_call_id)
    
    original_call_sid = transfer_context.original_call_sid if transfer_context else None
    caller_number = transfer_context.caller_number if transfer_context else None
    
    # Use original call SID for conference name if available, otherwise fall back to transfer call SID  
    base_call_sid = original_call_sid or call_sid
    conference_name = f"transfer-{base_call_sid}"
    
    logger.info(f"Using conference name: {conference_name}")
    
    # Publish Redis event for transfer answer completion
    try:
        # Get transfer coordinator and context
        transfer_coordinator = await get_transfer_coordinator()
        transfer_context = await transfer_coordinator.get_transfer_context(tool_call_id)
        
        if transfer_context:
            # Create transfer answered event
            from api.services.telephony.transfer_event_protocol import TransferEvent, TransferEventType
            
            transfer_event = TransferEvent(
                type=TransferEventType.TRANSFER_ANSWERED,
                tool_call_id=tool_call_id,
                workflow_run_id=transfer_context.workflow_run_id,
                original_call_sid=original_call_sid,
                transfer_call_sid=call_sid,
                conference_name=conference_name,
                message="Great! The person answered. Let me transfer you now.",
                status="success",
                action="transfer_success"
            )
            
            # Publish the event to Redis
            await transfer_coordinator.publish_transfer_event(transfer_event)
            logger.info(f"Published TRANSFER_ANSWERED event for {tool_call_id}")
            
        else:
            logger.warning(f"No transfer context found for {tool_call_id}")
            
    except Exception as e:
        logger.error(f"Error publishing transfer answered event for {tool_call_id}: {e}")
    
    # Return TwiML to put the answerer into the conference
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>You have answered a transfer call. Connecting you now.</Say>
    <Dial>
        <Conference>{conference_name}</Conference>
    </Dial>
</Response>"""
    
    return HTMLResponse(content=twiml, media_type="application/xml")


@router.post("/transfer-result/{tool_call_id}")
async def complete_transfer_function_call(tool_call_id: str, request: Request):
    """Webhook endpoint to complete the function call with transfer result"""
    form_data = await request.form()
    data = dict(form_data)
    
    call_status = data.get("CallStatus", "")
    call_sid = data.get("CallSid", "")
    
    logger.info(f"Transfer result webhook: {tool_call_id} status={call_status}")
    
    # Note: All transfer coordination now handled via Redis events
    
    # Skip "completed" status to avoid overriding successful transfer results
    # The "answered" status already handled the success case
    if call_status == "completed":
        logger.info(f"Ignoring 'completed' status for {tool_call_id} to avoid overriding previous results")
        return {"status": "ignored", "reason": "completed_status_filtered"}
    
    # Import required event classes
    from api.services.telephony.transfer_event_protocol import TransferEvent, TransferEventType
    
    # Get transfer context from Redis for additional information
    transfer_coordinator = await get_transfer_coordinator()
    transfer_context = await transfer_coordinator.get_transfer_context(tool_call_id)
    
    original_call_sid = transfer_context.original_call_sid if transfer_context else None
    caller_number = transfer_context.caller_number if transfer_context else None
    
    
    # Determine the result based on call status with user-friendly messaging
    if call_status == "answered":
        # Use original call SID for conference name if available, otherwise fall back to transfer call SID
        base_call_sid = original_call_sid or call_sid
        conference_name = f"transfer-{base_call_sid}"
        
        result = {
            "status": "success",
            "message": "Great! The person answered. Let me transfer you now.",
            "action": "transfer_success",
            "conference_id": conference_name,
            "transfer_call_sid": call_sid,  # The outbound transfer call SID
            "original_call_sid": original_call_sid,  # The original caller's SID
            "caller_number": caller_number,
            "end_call": False  # Continue with transfer
        }
    elif call_status == "no-answer":
        result = {
            "status": "transfer_failed",
            "reason": "no_answer",
            "message": "The transfer call was not answered. The person may be busy or unavailable right now.",
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True
        }
    elif call_status == "busy":
        result = {
            "status": "transfer_failed", 
            "reason": "busy",
            "message": "The transfer call encountered a busy signal. The person is likely on another call.",
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True
        }
    elif call_status == "failed":
        result = {
            "status": "transfer_failed",
            "reason": "call_failed",
            "message": "The transfer call failed to connect. There may be a network issue or the number is unavailable.",
            "action": "transfer_failed", 
            "call_sid": call_sid,
            "end_call": True
        }
    else:
        # Intermediate status (ringing, in-progress, etc.), don't complete yet
        logger.info(f"Received intermediate status {call_status}, waiting for final status")
        return {"status": "pending"}
    
    # Complete the function call with Redis event publishing
    try:
        # Determine event type based on result status
        if result["status"] == "success":
            event_type = TransferEventType.TRANSFER_COMPLETED
        elif result.get("reason") == "timeout":
            event_type = TransferEventType.TRANSFER_TIMEOUT
        else:
            event_type = TransferEventType.TRANSFER_FAILED
            
        # Create and publish transfer event  
        # Add caller_number to result if not already present
        if "caller_number" not in result and caller_number:
            result["caller_number"] = caller_number
            
        transfer_event = TransferEvent(
            type=event_type,
            tool_call_id=tool_call_id,
            workflow_run_id=0,  # TODO: Extract from context if needed
            original_call_sid=original_call_sid or "",
            transfer_call_sid=call_sid,
            conference_name=result.get("conference_id"),
            message=result.get("message", ""),
            status=result["status"],
            action=result.get("action", ""),
            reason=result.get("reason"),
            end_call=result.get("end_call", False)
        )
        
        # Publish the event via Redis
        await transfer_coordinator.publish_transfer_event(transfer_event)
        logger.info(f"Published {event_type} event for {tool_call_id}")
        
        
        # Clean up transfer context from Redis
        await transfer_coordinator.remove_transfer_context(tool_call_id)
        
        logger.info(f"Function call {tool_call_id} completed with result: {result['status']}")
        
    except Exception as e:
        logger.error(f"Error completing function call {tool_call_id}: {e}")
        
    return {"status": "completed", "result": result}


@router.post("/register-transfer-tool-call")
async def register_transfer_tool_call(request: Request):
    """Register a pending transfer function call for webhook completion"""
    data = await request.json()
    
    tool_call_id = data.get("tool_call_id")
    function_call_params = data.get("function_call_params")
    
    if not tool_call_id or not function_call_params:
        raise HTTPException(status_code=400, detail="Missing required fields")
    
    # Store the function call context for webhook completion
    pending_function_calls[tool_call_id] = (function_call_params, time.time())
    
    logger.info(f"Registered transfer tool call: {tool_call_id}")
    
    return {"status": "registered", "tool_call_id": tool_call_id}


    