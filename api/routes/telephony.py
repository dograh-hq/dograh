"""
Telephony routes - handles all telephony-related endpoints.
Consolidated from split modules for easier maintenance.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket
from loguru import logger
from pipecat.utils.context import set_current_run_id
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.future import select
from starlette.responses import HTMLResponse

from api.db import db_client
from api.db.models import OrganizationConfigurationModel, UserModel
from api.db.workflow_client import WorkflowClient
from api.db.workflow_run_client import WorkflowRunClient
from api.enums import CallType, OrganizationConfigurationKey, WorkflowRunState
from api.errors.telephony_errors import TelephonyError
from api.services.auth.depends import get_user
from api.services.campaign.call_dispatcher import campaign_call_dispatcher
from api.services.campaign.campaign_event_publisher import get_campaign_event_publisher
from api.services.quota_service import check_dograh_quota, check_dograh_quota_by_user_id
from api.services.telephony.factory import (
    get_all_telephony_providers,
    get_telephony_provider,
)
from api.utils.telephony_helper import (
    generic_hangup_response,
    normalize_webhook_data,
    numbers_match,
    parse_webhook_request,
)
from api.utils.tunnel import TunnelURLProvider

router = APIRouter(prefix="/telephony")


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
            detail="Phone number must be provided in request or set in user configuration",
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
    backend_endpoint = await TunnelURLProvider.get_tunnel_url()

    webhook_endpoint = provider.WEBHOOK_ENDPOINT

    webhook_url = (
        f"https://{backend_endpoint}/api/v1/telephony/{webhook_endpoint}"
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

    # Verify webhook signature if provided
    provider_instance = None
    if x_twilio_signature or x_vobiz_signature:
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()
        webhook_url = (
            f"https://{backend_endpoint}/api/v1/telephony/inbound/{workflow_id}"
        )

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
        else:
            logger.warning(
                f"No signature validation for provider {provider_class.PROVIDER_NAME}"
            )
            signature_valid = True

        logger.info(f"Signature validation result: {signature_valid}")
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
    workflow_id: int, user_id: int, workflow_run_id: int, organization_id: int
):
    """
    Handle initial webhook from telephony provider.
    Returns provider-specific response (e.g., TwiML for Twilio).
    """

    provider = await get_telephony_provider(organization_id)

    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )

    return HTMLResponse(content=response_content, media_type="application/xml")


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

    except Exception as e:
        logger.error(f"Error in WebSocket connection: {e}")
        await websocket.close(1011, "Internal server error")


@router.post("/twilio/status-callback/{workflow_run_id}")
async def handle_twilio_status_callback(
    workflow_run_id: int,
    request: Request,
    x_webhook_signature: Optional[str] = Header(None),
):
    """Handle Twilio-specific status callbacks."""

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
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()
        full_url = f"https://{backend_endpoint}/api/v1/telephony/twilio/status-callback/{workflow_run_id}"

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
    await _process_status_update(workflow_run_id, status_update, workflow_run)

    return {"status": "success"}


async def _process_status_update(
    workflow_run_id: int, status: StatusCallbackRequest, workflow_run: any
):
    """Process status updates from telephony providers."""

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
            run_id=workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
        )

    elif status.status in ["failed", "busy", "no-answer", "canceled"]:
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
    await _process_status_update(workflow_run_id, status_update, workflow_run)

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
    # TODO: Remove this debug logging after Vobiz team clarifies webhook authentication
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
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()
        webhook_url = f"https://{backend_endpoint}/api/v1/telephony/vobiz/hangup-callback/{workflow_run_id}"

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
    await _process_status_update(workflow_run_id, status_update, workflow_run)

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
    # TODO: Remove this debug logging after Vobiz team clarifies webhook authentication
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
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()
        webhook_url = f"https://{backend_endpoint}/api/v1/telephony/vobiz/ring-callback/{workflow_run_id}"

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
    await _process_status_update(workflow_run_id, status_update, workflow_run)

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
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()
        webhook_url = f"https://{backend_endpoint}/api/v1/telephony/vobiz/hangup-callback/workflow/{workflow_id}"

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

        await _process_status_update(workflow_run_id, status, workflow_run)

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
):
    """Handle inbound telephony calls from any supported provider with common processing"""
    logger.info(f"Inbound call received for workflow_id: {workflow_id}")

    try:
        webhook_data, data_source = await parse_webhook_request(request)
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
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()
        websocket_url = f"wss://{backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{workflow_context['user_id']}/{workflow_run_id}"
        response = await provider_class.generate_inbound_response(
            websocket_url, workflow_run_id
        )

        logger.info(
            f"Generated {normalized_data.provider} response for call {normalized_data.call_id}"
        )
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
