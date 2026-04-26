"""
Telephony routes - handles all telephony-related endpoints.
Consolidated from split modules for easier maintenance.
"""

import json
import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    WebSocket,
)
from loguru import logger
from pydantic import BaseModel, field_validator
from starlette.websockets import WebSocketDisconnect

from api.db import db_client
from api.db.models import UserModel
from api.enums import CallType, WorkflowRunState
from api.errors.telephony_errors import TelephonyError
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.quota_service import check_dograh_quota, check_dograh_quota_by_user_id
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import (
    get_all_telephony_providers,
    get_default_telephony_provider,
    get_telephony_provider,
    get_telephony_provider_by_id,
)
from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_helper import (
    generic_hangup_response,
    normalize_webhook_data,
    numbers_match,
    parse_webhook_request,
)
from pipecat.utils.run_context import set_current_run_id

router = APIRouter(prefix="/telephony")


class InitiateCallRequest(BaseModel):
    workflow_id: int
    workflow_run_id: int | None = None
    phone_number: str | None = None
    # Optional explicit telephony config to use for the test call. If omitted,
    # falls back to the user's per-user default (when set), then the org default.
    telephony_configuration_id: int | None = None


@router.post(
    "/initiate-call",
    **sdk_expose(
        method="test_phone_call",
        description="Place a test call from a workflow to a phone number.",
    ),
)
async def initiate_call(
    request: InitiateCallRequest, user: UserModel = Depends(get_user)
):
    """Initiate a call using the configured telephony provider from web browser. This is
    supposed to be a test call method for the draft version of the agent."""

    user_configuration = await db_client.get_user_configurations(user.id)

    # Resolve which telephony config to use, in order of precedence:
    # 1. explicit request value, 2. per-user default (when set), 3. org default.
    user_default_cfg_id = (
        (user_configuration.configuration or {}).get("test_telephony_configuration_id")
        if user_configuration
        else None
    )
    telephony_configuration_id = (
        request.telephony_configuration_id or user_default_cfg_id
    )

    if telephony_configuration_id:
        cfg = await db_client.get_telephony_configuration_for_org(
            telephony_configuration_id, user.selected_organization_id
        )
        if not cfg:
            raise HTTPException(
                status_code=400, detail="telephony_configuration_not_found"
            )
        provider = await get_telephony_provider_by_id(telephony_configuration_id)
    else:
        try:
            provider = await get_default_telephony_provider(
                user.selected_organization_id
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="telephony_not_configured")
        default_cfg = await db_client.get_default_telephony_configuration(
            user.selected_organization_id
        )
        telephony_configuration_id = default_cfg.id if default_cfg else None

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

    phone_number = request.phone_number or user_configuration.test_phone_number

    if not phone_number:
        raise HTTPException(
            status_code=400,
            detail="Phone number must be provided in request or set in user "
            "configuration",
        )

    workflow_run_id = request.workflow_run_id

    if not workflow_run_id:
        # Fetch workflow to merge template context variables (e.g. caller_number,
        # called_number set in workflow settings for testing pre-call data fetch)
        workflow = await db_client.get_workflow_by_id(request.workflow_id)
        template_vars = (workflow.template_context_variables or {}) if workflow else {}

        numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
        workflow_run_name = f"WR-TEL-OUT-{numeric_suffix:08d}"
        workflow_run = await db_client.create_workflow_run(
            workflow_run_name,
            request.workflow_id,
            workflow_run_mode,
            user_id=user.id,
            call_type=CallType.OUTBOUND,
            initial_context={
                **template_vars,
                "phone_number": phone_number,
                "called_number": phone_number,
                "provider": provider.PROVIDER_NAME,
                "telephony_configuration_id": telephony_configuration_id,
            },
            use_draft=True,
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

    # Store provider metadata and caller_number in workflow run context
    gathered_context = {
        "provider": provider.PROVIDER_NAME,
        **(result.provider_metadata or {}),
    }
    # Merge caller_number into initial_context now that we know which number was used
    updated_initial_context = {
        **(workflow_run.initial_context or {}),
        "called_number": phone_number,
        "telephony_configuration_id": telephony_configuration_id,
    }
    if result.caller_number:
        updated_initial_context["caller_number"] = result.caller_number
    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        gathered_context=gathered_context,
        initial_context=updated_initial_context,
    )

    return {"message": f"Call initiated successfully with run name {workflow_run_name}"}


async def _verify_organization_phone_number(
    phone_number: str,
    organization_id: int,
    telephony_configuration_id: int,
    provider: str,
    to_country: str = None,
    from_country: str = None,
) -> Optional[int]:
    """Verify the called number is registered to the matched config and return
    its ``telephony_phone_numbers.id``, or None when no row matches.

    Primary path: deterministic E.164 / SIP lookup via the new phone-number table.
    Legacy fallback: ``numbers_match()`` over the matched config's active numbers,
    so non-E.164 rows that survived the migration still route correctly.
    """
    try:
        match = await db_client.find_active_phone_number_for_inbound(
            organization_id, phone_number, provider, country_hint=to_country
        )
        if match and match.telephony_configuration_id == telephony_configuration_id:
            logger.info(
                f"Phone number {phone_number} matched row {match.id} for org "
                f"{organization_id} / config {telephony_configuration_id}"
            )
            return match.id

        # Legacy fallback: scan the matched config's active numbers and apply
        # the country-aware fuzzy matcher (covers non-E.164 storage).
        rows = await db_client.list_phone_numbers_for_config(telephony_configuration_id)
        for row in rows:
            if not row.is_active:
                continue
            if numbers_match(phone_number, row.address, to_country, from_country):
                logger.info(
                    f"Phone number {phone_number} matched (fuzzy) row {row.id} "
                    f"for config {telephony_configuration_id}"
                )
                return row.id

        logger.warning(
            f"Phone number {phone_number} not registered to config "
            f"{telephony_configuration_id} (org={organization_id}, "
            f"to_country={to_country}, from_country={from_country})"
        )
        return None

    except Exception as e:
        logger.error(
            f"Error verifying phone number {phone_number} for organization "
            f"{organization_id} / config {telephony_configuration_id}: {e}"
        )
        return None


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
    x_plivo_signature: str = None,
    x_plivo_signature_ma: str = None,
    x_plivo_signature_nonce: str = None,
    x_vobiz_signature: str = None,
    x_vobiz_timestamp: str = None,
    x_cx_apikey: str = None,
    telnyx_signature: str = None,
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

    # Resolve which of the org's configs this webhook came from (account_id match).
    (
        validation_result,
        telephony_configuration_id,
    ) = await _resolve_inbound_telephony_config(
        organization_id, provider_class, normalized_data.account_id
    )
    if validation_result != TelephonyError.VALID:
        return False, validation_result, {}, None

    # Verify the called number is registered to that config.
    phone_number_id = await _verify_organization_phone_number(
        normalized_data.to_number,
        organization_id,
        telephony_configuration_id,
        provider_class.PROVIDER_NAME,
        normalized_data.to_country,
        normalized_data.from_country,
    )
    if phone_number_id is None:
        return False, TelephonyError.PHONE_NUMBER_NOT_CONFIGURED, {}, None

    # Verify webhook signature/API key if provided
    provider_instance = None
    if (
        x_twilio_signature
        or x_plivo_signature
        or x_plivo_signature_ma
        or x_vobiz_signature
        or x_cx_apikey
        or telnyx_signature
    ):
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = f"{backend_endpoint}/api/v1/telephony/inbound/{workflow_id}"

        # Use the credentials of the *matched* config so signature verification
        # works in orgs that have multiple configs of the same provider.
        provider_instance = await get_telephony_provider_by_id(
            telephony_configuration_id
        )

        if provider_class.PROVIDER_NAME == "twilio" and x_twilio_signature:
            logger.info(f"Verifying Twilio signature for URL: {webhook_url}")
            signature_valid = await provider_instance.verify_inbound_signature(
                webhook_url, webhook_data, x_twilio_signature
            )
        elif provider_class.PROVIDER_NAME == "plivo" and (
            x_plivo_signature or x_plivo_signature_ma
        ):
            logger.info(f"Verifying Plivo signature for URL: {webhook_url}")
            signature_valid = await provider_instance.verify_inbound_signature(
                webhook_url,
                webhook_data,
                x_plivo_signature or x_plivo_signature_ma,
                x_plivo_signature_nonce,
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
        elif provider_class.PROVIDER_NAME == "telnyx" and telnyx_signature:
            logger.info(f"Verifying Telnyx signature for URL: {webhook_url}")
            signature_valid = await provider_instance.verify_inbound_signature(
                webhook_url, webhook_data, telnyx_signature
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
        "telephony_configuration_id": telephony_configuration_id,
        "from_phone_number_id": phone_number_id,
    }
    return (
        True,
        "",
        workflow_context,
        provider_instance,
    )  # TODO: do we still need instance in the client code


async def _create_inbound_workflow_run(
    workflow_id: int,
    user_id: int,
    provider: str,
    normalized_data,
    data_source: str,
    telephony_configuration_id: int,
    from_phone_number_id: Optional[int] = None,
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
            "account_id": normalized_data.account_id,
            "provider": provider,
            "data_source": data_source,
            "from_country": normalized_data.from_country,
            "to_country": normalized_data.to_country,
            "raw_webhook_data": normalized_data.raw_data,
            "telephony_configuration_id": telephony_configuration_id,
            "from_phone_number_id": from_phone_number_id,
        },
        gathered_context={
            "call_id": call_id,
        },
    )

    logger.info(
        f"Created inbound workflow run {workflow_run.id} for {provider} call {call_id}"
    )
    return workflow_run.id


async def _resolve_inbound_telephony_config(
    organization_id: int, provider_class, account_id: str
) -> tuple[TelephonyError, Optional[int]]:
    """Find which of the org's telephony configs the inbound webhook came from.

    Returns ``(VALID, config_id)`` on success or ``(error, None)`` otherwise.
    Replaces the single-config check that assumed one provider per org.
    """
    from api.services.telephony.factory import find_telephony_config_for_inbound

    try:
        candidates = await db_client.list_telephony_configurations_by_provider(
            organization_id, provider_class.PROVIDER_NAME
        )
        if not candidates:
            logger.warning(
                f"No {provider_class.PROVIDER_NAME} configuration for org "
                f"{organization_id}"
            )
            return TelephonyError.PROVIDER_MISMATCH, None

        match = await find_telephony_config_for_inbound(
            organization_id, provider_class.PROVIDER_NAME, account_id
        )
        if not match:
            logger.warning(
                f"Account validation failed for {provider_class.PROVIDER_NAME}: "
                f"webhook account_id={account_id} (org {organization_id})"
            )
            return TelephonyError.ACCOUNT_VALIDATION_FAILED, None

        config_id, _ = match
        return TelephonyError.VALID, config_id

    except Exception as e:
        logger.error(f"Exception during account validation: {e}")
        return TelephonyError.ACCOUNT_VALIDATION_FAILED, None


@router.websocket("/ws/ari")
async def websocket_ari_endpoint(websocket: WebSocket):
    """WebSocket endpoint for ARI chan_websocket external media.

    Asterisk connects here via chan_websocket. Routing params are passed as
    query params (appended by the v() dial string option in externalMedia).
    """
    workflow_id = websocket.query_params.get("workflow_id")
    user_id = websocket.query_params.get("user_id")
    workflow_run_id = websocket.query_params.get("workflow_run_id")

    if not workflow_id or not user_id or not workflow_run_id:
        logger.error(
            f"ARI WebSocket missing query params: "
            f"workflow_id={workflow_id}, user_id={user_id}, workflow_run_id={workflow_run_id}"
        )
        await websocket.close(code=4400, reason="Missing required query params")
        return

    # Accept with "media" subprotocol — chan_websocket sends
    # Sec-WebSocket-Protocol: media and requires it echoed back.
    await websocket.accept(subprotocol="media")

    await _handle_telephony_websocket(
        websocket, int(workflow_id), int(user_id), int(workflow_run_id)
    )


@router.websocket("/ws/{workflow_id}/{user_id}/{workflow_run_id}")
async def websocket_endpoint(
    websocket: WebSocket, workflow_id: int, user_id: int, workflow_run_id: int
):
    """WebSocket endpoint for real-time call handling - routes to provider-specific handlers."""
    await websocket.accept()
    await _handle_telephony_websocket(websocket, workflow_id, user_id, workflow_run_id)


async def _handle_telephony_websocket(
    websocket: WebSocket, workflow_id: int, user_id: int, workflow_run_id: int
):
    """Shared WebSocket handler logic (connection already accepted)."""
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


@router.post("/inbound/{workflow_id}")
async def handle_inbound_telephony(
    workflow_id: int,
    request: Request,
    x_twilio_signature: Optional[str] = Header(None),
    x_plivo_signature_v3: Optional[str] = Header(None),
    x_plivo_signature_ma_v3: Optional[str] = Header(None),
    x_plivo_signature_v3_nonce: Optional[str] = Header(None),
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
    x_cx_apikey: Optional[str] = Header(None),
    telnyx_signature: Optional[str] = Header(None, alias="telnyx-signature-ed25519"),
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
            x_plivo_signature_v3,
            x_plivo_signature_ma_v3,
            x_plivo_signature_v3_nonce,
            x_vobiz_signature,
            x_vobiz_timestamp,
            x_cx_apikey,
            telnyx_signature,
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
            telephony_configuration_id=workflow_context["telephony_configuration_id"],
            from_phone_number_id=workflow_context.get("from_phone_number_id"),
        )

        # Generate response URLs
        backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()
        websocket_url = f"{wss_backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{workflow_context['user_id']}/{workflow_run_id}"

        # Telnyx requires answering the call via REST API (not via webhook response)
        if provider_class.PROVIDER_NAME == "telnyx":
            # Get provider instance with credentials if not already loaded
            if not provider_instance:
                provider_instance = await get_telephony_provider_by_id(
                    workflow_context["telephony_configuration_id"]
                )

            events_url = (
                f"{backend_endpoint}/api/v1/telephony/telnyx/events/{workflow_run_id}"
            )

            try:
                await provider_instance.answer_and_stream(
                    call_control_id=normalized_data.call_id,
                    stream_url=websocket_url,
                    webhook_url=events_url,
                )
            except Exception as e:
                logger.error(f"Failed to answer Telnyx inbound call: {e}")
                return provider_class.generate_error_response(
                    "ANSWER_FAILED", "Failed to answer call"
                )

            logger.info(
                f"Answered Telnyx inbound call {normalized_data.call_id} for workflow_run {workflow_run_id}"
            )
            return {"status": "ok"}

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


class TransferCallRequest(BaseModel):
    """Request model for initiating a call transfer."""

    destination: str  # E.164 format phone number (required)
    organization_id: int  # Organization ID for provider configuration
    transfer_id: str  # Unique identifier for tracking this transfer
    conference_name: str  # Conference name for the transfer
    timeout: Optional[int] = 20  # seconds to wait for answer

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, destination: str) -> str:
        """Validate destination is in E.164 format."""
        import re

        if not destination or not destination.strip():
            raise ValueError("Destination phone number is required")

        E164_PHONE_REGEX = r"^\+[1-9]\d{1,14}$"
        if not re.match(E164_PHONE_REGEX, destination.strip()):
            raise ValueError(
                f"Invalid phone number format: {destination}. Must be E.164 format (e.g., +1234567890)"
            )

        return destination.strip()


@router.post("/call-transfer")
async def initiate_call_transfer(request: TransferCallRequest):
    """Initiate a call transfer via the telephony provider.

    This endpoint only initiates the outbound call. Transfer context
    (original_call_sid, etc.) is stored by the caller
    before invoking this endpoint.
    """
    logger.info(
        f"Starting call transfer to {request.destination} with transfer_id: {request.transfer_id}"
    )

    try:
        try:
            provider = await get_telephony_provider(request.organization_id)
        except ValueError as e:
            logger.error(f"Transfer provider validation failed: {e}")
            raise HTTPException(
                status_code=400, detail=f"Call transfer not supported: {str(e)}"
            )

        if not provider.supports_transfers():
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{provider.PROVIDER_NAME}' does not support call transfers",
            )

        if not provider.validate_config():
            logger.error(f"Provider {provider.PROVIDER_NAME} configuration is invalid")
            raise HTTPException(
                status_code=400,
                detail=f"Telephony provider '{provider.PROVIDER_NAME}' is not properly configured for transfers",
            )

        logger.info(f"Initiating transfer call via {provider.PROVIDER_NAME} provider")
        try:
            transfer_result = await provider.transfer_call(
                destination=request.destination,
                transfer_id=request.transfer_id,
                conference_name=request.conference_name,
                timeout=request.timeout,
            )
        except NotImplementedError as e:
            logger.error(
                f"Provider {provider.PROVIDER_NAME} doesn't support transfers: {e}"
            )
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{provider.PROVIDER_NAME}' does not support call transfers",
            )
        except Exception as e:
            logger.error(f"Provider transfer call failed: {e}")
            raise HTTPException(
                status_code=500, detail=f"Transfer call failed: {str(e)}"
            )

        call_sid = transfer_result.get("call_sid")
        logger.info(f"Transfer call initiated successfully: {call_sid}")
        logger.debug(f"Transfer result: {transfer_result}")

        return {
            "status": "transfer_initiated",
            "call_id": call_sid,
            "message": f"Calling {request.destination}...",
            "transfer_id": request.transfer_id,
            "provider": provider.PROVIDER_NAME,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during transfer call: {e}")
        raise HTTPException(
            status_code=500, detail=f"Internal error during transfer: {str(e)}"
        )


@router.post("/transfer-result/{transfer_id}")
async def complete_transfer_function_call(transfer_id: str, request: Request):
    """Webhook endpoint to complete the function call with transfer result.

    Called by Twilio's StatusCallback when the transfer call status changes.
    """
    form_data = await request.form()
    data = dict(form_data)

    call_status = data.get("CallStatus", "")
    call_sid = data.get("CallSid", "")

    logger.info(
        f"Transfer result(call status) webhook: {transfer_id} status={call_status}"
    )

    # Get transfer context from Redis for additional information
    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)

    original_call_sid = transfer_context.original_call_sid if transfer_context else None
    conference_name = transfer_context.conference_name if transfer_context else None

    # Determine the result based on call status with user-friendly messaging
    if call_status in ("in-progress", "answered"):
        result = {
            "status": "success",
            "message": "Great! The destination number answered. Let me transfer you now.",
            "action": "destination_answered",
            "conference_id": conference_name,
            "transfer_call_sid": call_sid,  # The outbound transfer call SID
            "original_call_sid": original_call_sid,  # The original caller's SID
            "end_call": False,  # Continue with transfer
        }
    elif call_status == "no-answer":
        result = {
            "status": "transfer_failed",
            "reason": "no_answer",
            "message": "The transfer call was not answered. The person may be busy or unavailable right now.",
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True,
        }
    elif call_status == "busy":
        result = {
            "status": "transfer_failed",
            "reason": "busy",
            "message": "The transfer call encountered a busy signal. The person is likely on another call.",
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True,
        }
    elif call_status == "failed":
        result = {
            "status": "transfer_failed",
            "reason": "call_failed",
            "message": "The transfer call failed to connect. There may be a network issue or the number is unavailable.",
            "action": "transfer_failed",
            "call_sid": call_sid,
            "end_call": True,
        }
    else:
        # Intermediate status (ringing, in-progress, etc.), don't complete yet
        logger.info(
            f"Received intermediate status {call_status}, waiting for final status"
        )
        return {"status": "pending"}

    # Complete the function call with Redis event publishing
    try:
        # Determine event type based on result status
        if result["status"] == "success":
            event_type = TransferEventType.DESTINATION_ANSWERED
        else:
            event_type = TransferEventType.TRANSFER_FAILED

        transfer_event = TransferEvent(
            type=event_type,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid or "",
            transfer_call_sid=call_sid,
            conference_name=conference_name,
            message=result.get("message", ""),
            status=result["status"],
            action=result.get("action", ""),
            reason=result.get("reason"),
        )

        # Publish the event via Redis
        await call_transfer_manager.publish_transfer_event(transfer_event)
        logger.info(
            f"Published {event_type} event for {transfer_id} with result: {result['status']}"
        )

    except Exception as e:
        logger.error(f"Error completing transfer {transfer_id}: {e}")

    return {"status": "completed", "result": result}


# Mount per-provider routers (webhook, status callbacks, answer URLs).
#
# Each provider's routes live at ``providers/<name>/routes.py`` and expose
# a module-level ``router``. We discover them through the registry rather
# than pre-importing them from each provider's __init__.py so that the
# (heavy) route module — which transitively depends on status_processor,
# campaign helpers, etc. — is only loaded when the HTTP layer is actually
# being wired up, not when someone merely asks for a TelephonyProvider
# class. This is what keeps the package init free of cycles.
def _mount_provider_routers() -> None:
    import importlib

    from api.services.telephony import registry as _telephony_registry

    for spec in _telephony_registry.all_specs():
        try:
            module = importlib.import_module(
                f"api.services.telephony.providers.{spec.name}.routes"
            )
        except ModuleNotFoundError:
            # Provider has no routes (e.g. ARI, which only has a WebSocket).
            continue
        provider_router = getattr(module, "router", None)
        if provider_router is not None:
            router.include_router(provider_router)


_mount_provider_routers()
