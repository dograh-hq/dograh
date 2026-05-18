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
    HTTPException,
    Request,
    WebSocket,
)
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from pydantic import BaseModel
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
    get_telephony_provider_by_id,
    get_telephony_provider_for_run,
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

router = APIRouter(prefix="/telephony")


class InitiateCallRequest(BaseModel):
    workflow_id: int
    workflow_run_id: int | None = None
    phone_number: str | None = None
    # Optional explicit telephony config to use for the test call. If omitted,
    # falls back to the user's per-user default (when set), then the org default.
    telephony_configuration_id: int | None = None
    # Optional caller-ID phone number to dial out from. Must belong to the
    # resolved telephony configuration; otherwise the provider picks one.
    from_phone_number_id: int | None = None


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

    # Resolve which telephony config to use: explicit request value, otherwise
    # the org's default outbound config.
    telephony_configuration_id = request.telephony_configuration_id

    if telephony_configuration_id:
        try:
            provider = await get_telephony_provider_by_id(
                telephony_configuration_id, user.selected_organization_id
            )
        except ValueError:
            raise HTTPException(
                status_code=400, detail="telephony_configuration_not_found"
            )
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

    # Check Dograh quota before initiating the call (apply per-workflow
    # model_overrides so the keys we will actually use are the ones checked).
    quota_result = await check_dograh_quota(user, workflow_id=request.workflow_id)
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
        workflow = await db_client.get_workflow(
            request.workflow_id, organization_id=user.selected_organization_id
        )
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        template_vars = workflow.template_context_variables or {}

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

    # Resolve optional caller-ID. The config has already been validated against
    # the user's organization, so filtering by config_id is sufficient for
    # tenant isolation.
    from_number: str | None = None
    if request.from_phone_number_id is not None:
        if telephony_configuration_id is None:
            raise HTTPException(
                status_code=400,
                detail="from_phone_number_id_requires_telephony_configuration",
            )
        phone_row = await db_client.get_phone_number_for_config(
            request.from_phone_number_id, telephony_configuration_id
        )
        if not phone_row or not phone_row.is_active:
            raise HTTPException(status_code=400, detail="from_phone_number_not_found")
        from_number = phone_row.address_normalized

    # Initiate call via provider
    result = await provider.initiate_call(
        to_number=phone_number,
        webhook_url=webhook_url,
        workflow_run_id=workflow_run_id,
        from_number=from_number,
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
    headers: dict,
    raw_body: str = "",
) -> tuple[bool, TelephonyError, dict, object]:
    """
    Validate all aspects of inbound request.
    Returns: (is_valid, error_type, workflow_context, provider_instance)
    """
    from api.services.telephony import registry as telephony_registry

    workflow = await db_client.get_workflow(workflow_id)
    if not workflow:
        return False, TelephonyError.WORKFLOW_NOT_FOUND, {}, None

    organization_id = workflow.organization_id
    user_id = workflow.user_id
    provider = normalized_data.provider

    # Primary path: one combined query that resolves config + phone number
    # together (joins configs and phone_numbers with provider, account_id,
    # and called-number filters). Falls back to the two-step config-then-
    # phone resolution to cover providers without account_id (ARI) and
    # legacy non-E.164 stored addresses.
    spec = telephony_registry.get_optional(provider_class.PROVIDER_NAME)
    account_field = spec.account_id_credential_field if spec else ""

    telephony_configuration_id: Optional[int] = None
    phone_number_id: Optional[int] = None

    if account_field and normalized_data.account_id:
        match = await db_client.find_inbound_route_by_account(
            provider=provider_class.PROVIDER_NAME,
            account_id_field=account_field,
            account_id=normalized_data.account_id,
            to_number=normalized_data.to_number,
            country_hint=normalized_data.to_country,
            organization_id=organization_id,
        )
        if match:
            cfg_row, phone_row = match
            telephony_configuration_id = cfg_row.id
            phone_number_id = phone_row.id

    if telephony_configuration_id is None:
        (
            validation_result,
            telephony_configuration_id,
        ) = await _resolve_inbound_telephony_config(
            organization_id, provider_class, normalized_data.account_id
        )
        if validation_result != TelephonyError.VALID:
            return False, validation_result, {}, None

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

    # Verify webhook signature using the matched config's credentials. The
    # provider extracts its own signature/timestamp/nonce headers from the
    # dict, so this dispatcher stays generic.
    backend_endpoint, _ = await get_backend_endpoints()
    webhook_url = f"{backend_endpoint}/api/v1/telephony/inbound/{workflow_id}"
    provider_instance = await get_telephony_provider_by_id(
        telephony_configuration_id, organization_id
    )
    signature_valid = await provider_instance.verify_inbound_signature(
        webhook_url, webhook_data, headers, raw_body
    )
    logger.info(f"Signature validation for {provider}: {signature_valid}")
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
    return (True, "", workflow_context, provider_instance)


async def _create_inbound_workflow_run(
    workflow_id: int,
    user_id: int,
    provider: str,
    normalized_data,
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
            "provider": provider,
            "telephony_configuration_id": telephony_configuration_id,
        },
        gathered_context={
            "call_id": call_id,
        },
        logs={
            "inbound_webhook": {
                "account_id": normalized_data.account_id,
                "from_country": normalized_data.from_country,
                "to_country": normalized_data.to_country,
                "from_phone_number_id": from_phone_number_id,
                "raw_webhook_data": normalized_data.raw_data,
            },
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

        provider = await get_telephony_provider_for_run(
            workflow_run, workflow.organization_id
        )

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


@router.post("/inbound/run")
async def handle_inbound_run(request: Request):
    """Workflow-agnostic inbound dispatcher.

    All providers can point a single webhook at this endpoint instead of one
    URL per workflow. The dispatcher resolves the org from the webhook's
    account_id and the workflow from the called number's
    ``inbound_workflow_id``. This is what ``configure_inbound`` writes into
    each provider's resource so per-workflow webhook bookkeeping disappears.

    Provider-specific signature/timestamp headers are not enumerated here —
    each provider's ``verify_inbound_signature`` reads its own headers from
    the dict, so adding a new provider doesn't require changes to this route.
    """
    from api.services.telephony import registry as telephony_registry

    logger.info("Inbound /run dispatch received")

    try:
        webhook_data, raw_body = await parse_webhook_request(request)
        headers = dict(request.headers)

        provider_class = await _detect_provider(webhook_data, headers)
        if not provider_class:
            logger.error("Unable to detect provider for /inbound/run webhook")
            return generic_hangup_response()

        normalized_data = normalize_webhook_data(provider_class, webhook_data)
        logger.info(
            f"/inbound/run normalized data — provider={normalized_data.provider} "
            f"to={normalized_data.to_number} from={normalized_data.from_number}"
        )

        if normalized_data.direction != "inbound":
            logger.warning(
                f"Non-inbound call on /inbound/run: {normalized_data.direction}"
            )
            return generic_hangup_response()

        # 1. Resolve (config, phone_number) in a single SQL roundtrip that
        # joins telephony_configurations and telephony_phone_numbers and
        # filters on (provider, credentials[account_id_field], called number
        # canonical address, is_active). The phone-number row's existence in
        # the matched config simultaneously identifies the org — we never
        # match a config from one org against a phone owned by another.
        spec = telephony_registry.get_optional(provider_class.PROVIDER_NAME)
        account_field = spec.account_id_credential_field if spec else ""

        match = await db_client.find_inbound_route_by_account(
            provider=provider_class.PROVIDER_NAME,
            account_id_field=account_field,
            account_id=normalized_data.account_id or "",
            to_number=normalized_data.to_number,
            country_hint=normalized_data.to_country,
        )

        if not match:
            logger.warning(
                f"/inbound/run: no inbound route matched "
                f"provider={provider_class.PROVIDER_NAME} "
                f"account_id={normalized_data.account_id} "
                f"to={normalized_data.to_number}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.PHONE_NUMBER_NOT_CONFIGURED
            )

        config, phone_row = match
        telephony_configuration_id = config.id

        if not phone_row.inbound_workflow_id:
            logger.warning(
                f"/inbound/run: number {normalized_data.to_number} has no "
                f"inbound_workflow_id assigned"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.WORKFLOW_NOT_FOUND
            )

        workflow_id = phone_row.inbound_workflow_id
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=config.organization_id
        )
        if not workflow:
            logger.warning(
                f"/inbound/run: workflow not found {workflow_id} for org {config.organization_id}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.WORKFLOW_NOT_FOUND
            )
        user_id = workflow.user_id

        # 3. Verify webhook signature against the matched config's credentials.
        backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()
        webhook_url = f"{backend_endpoint}/api/v1/telephony/inbound/run"
        provider_instance = await get_telephony_provider_by_id(
            telephony_configuration_id, config.organization_id
        )
        signature_valid = await provider_instance.verify_inbound_signature(
            webhook_url, webhook_data, headers, raw_body
        )
        if not signature_valid:
            logger.warning(
                f"/inbound/run: signature validation failed for "
                f"{provider_class.PROVIDER_NAME}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.SIGNATURE_VALIDATION_FAILED
            )

        # 4. Quota check (use the workflow's model_overrides if set).
        quota_result = await check_dograh_quota_by_user_id(
            user_id, workflow_id=workflow_id
        )
        if not quota_result.has_quota:
            logger.warning(
                f"User {user_id} has exceeded quota: {quota_result.error_message}"
            )
            return provider_class.generate_validation_error_response(
                TelephonyError.QUOTA_EXCEEDED
            )

        # 5. Create workflow run + return provider-shaped response.
        workflow_run_id = await _create_inbound_workflow_run(
            workflow_id,
            user_id,
            provider_class.PROVIDER_NAME,
            normalized_data,
            telephony_configuration_id=telephony_configuration_id,
            from_phone_number_id=phone_row.id,
        )

        websocket_url = (
            f"{wss_backend_endpoint}/api/v1/telephony/ws/"
            f"{workflow_id}/{user_id}/{workflow_run_id}"
        )

        return await provider_instance.start_inbound_stream(
            websocket_url=websocket_url,
            workflow_run_id=workflow_run_id,
            normalized_data=normalized_data,
            backend_endpoint=backend_endpoint,
        )

    except ValueError as e:
        logger.error(f"/inbound/run request parsing error: {e}")
        return generic_hangup_response()
    except Exception as e:
        logger.error(f"/inbound/run unexpected error: {e}")
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


@router.post("/inbound/{workflow_id}", deprecated=True)
async def handle_inbound_telephony(
    workflow_id: int,
    request: Request,
):
    """[LEGACY] Per-workflow inbound webhook.

    Superseded by ``POST /inbound/run``, which resolves the workflow from
    the called number's ``inbound_workflow_id`` and lets a single webhook
    URL serve every workflow in the org. New integrations should point
    their provider at ``/inbound/run``; this route is kept only for
    existing provider configurations that still encode ``workflow_id``
    in the URL.
    """
    logger.info(
        f"[legacy /inbound/{{workflow_id}}] Inbound call received for workflow_id: {workflow_id}"
    )

    try:
        webhook_data, raw_body = await parse_webhook_request(request)
        logger.info(f"Inbound call data: {dict(webhook_data)}")
        headers = dict(request.headers)

        # Detect provider and normalize data
        provider_class = await _detect_provider(webhook_data, headers)
        if not provider_class:
            logger.error("Unable to detect provider for webhook")
            return generic_hangup_response()

        normalized_data = normalize_webhook_data(provider_class, webhook_data)

        logger.info(f"Inbound call - Provider: {normalized_data.provider}")
        logger.info(f"Normalized data: {normalized_data}")

        # Validate inbound direction
        if normalized_data.direction != "inbound":
            logger.warning(f"Non-inbound call received: {normalized_data.direction}")
            return generic_hangup_response()

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
            headers,
            raw_body,
        )

        if not is_valid:
            logger.error(f"Request validation failed: {error_type}")
            return provider_class.generate_validation_error_response(error_type)

        # Check quota before processing (apply per-workflow model_overrides).
        user_id = workflow_context["user_id"]
        quota_result = await check_dograh_quota_by_user_id(
            user_id, workflow_id=workflow_id
        )
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
            telephony_configuration_id=workflow_context["telephony_configuration_id"],
            from_phone_number_id=workflow_context.get("from_phone_number_id"),
        )

        # Generate response URLs
        backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()
        websocket_url = f"{wss_backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{workflow_context['user_id']}/{workflow_run_id}"

        response = await provider_instance.start_inbound_stream(
            websocket_url=websocket_url,
            workflow_run_id=workflow_run_id,
            normalized_data=normalized_data,
            backend_endpoint=backend_endpoint,
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
