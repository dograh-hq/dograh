"""WhatsApp webhook routes.

This module handles incoming webhooks from Meta's WhatsApp Business Calling API,
including call lifecycle events (connect, terminate), permission callbacks, and
webhook verification, connecting incoming WhatsApp calls directly to Dograh's
WebRTC AI voice pipeline.
"""

import asyncio
import hashlib
import hmac
import json
from typing import Any, Dict, Optional, Tuple

import aiohttp
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from starlette.responses import PlainTextResponse

from api.constants import (
    ENABLE_COTURN,
    FORCE_TURN_RELAY,
    REDIS_URL,
    WHATSAPP_WEBHOOK_VERIFY_TOKEN,
)
from api.db import db_client
from api.db.models import (
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    WorkflowRunModel,
)
from api.enums import CallType, TelephonyCallStatus, WorkflowRunState
from api.routes.turn_credentials import (
    TURN_HOST,
    TURN_PORT,
    TURN_SECRET,
    generate_turn_credentials,
)
from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    call_concurrency,
)
from api.services.pipecat.run_pipeline import run_pipeline_smallwebrtc
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow_run_failure import mark_workflow_run_failed
from api.utils.telephony_address import normalize_telephony_address
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.whatsapp.api import (
    WhatsAppConnectCall,
    WhatsAppWebhookRequest,
)
from pipecat.transports.whatsapp.client import WhatsAppClient

router = APIRouter(prefix="/whatsapp")

REDIS_TERMINATE_CHANNEL = "whatsapp:call:terminate"
WHATSAPP_CALL_KEY_PREFIX = "whatsapp:call:"

# Active in-memory registry of ongoing WhatsApp WebRTC calls on this worker:
# call_id -> (SmallWebRTCConnection, workflow_run_id, organization_id, phone_number_id)
_active_connections: Dict[str, Tuple[SmallWebRTCConnection, int, int, str]] = {}
_background_tasks: set[asyncio.Task] = set()

# Reusable aiohttp session and cached clients per phone_number_id
_http_session: Optional[aiohttp.ClientSession] = None
_clients: Dict[str, WhatsAppClient] = {}

_redis_client: Optional[aioredis.Redis] = None
_redis_subscriber_task: Optional[asyncio.Task] = None


async def _get_redis() -> Optional[aioredis.Redis]:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning(f"[WhatsApp] Failed to connect to Redis: {e}")
            return None
    return _redis_client


def _ensure_redis_subscriber() -> None:
    global _redis_subscriber_task
    if _redis_subscriber_task is None or _redis_subscriber_task.done():
        _redis_subscriber_task = asyncio.create_task(_listen_for_remote_terminates())


async def _listen_for_remote_terminates() -> None:
    """Listen for cross-worker terminate events on Redis pub/sub and disconnect local peer."""
    while True:
        redis = None
        pubsub = None
        try:
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            pubsub = redis.pubsub()
            await pubsub.subscribe(REDIS_TERMINATE_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    target_call_id = data.get("call_id")
                    if target_call_id and target_call_id in _active_connections:
                        entry = _active_connections.pop(target_call_id, None)
                        if entry:
                            conn = entry[0]
                            try:
                                await conn.disconnect()
                                logger.info(
                                    f"[WhatsApp] Peer connection closed via cross-worker terminate for {target_call_id}"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"[WhatsApp] Error during cross-worker peer disconnect: {e}"
                                )
                except Exception as e:
                    logger.warning(f"[WhatsApp] Error handling cross-worker terminate message: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(
                f"[WhatsApp] Redis terminate subscriber error: {e}, reconnecting in 5s..."
            )
        finally:
            if pubsub:
                try:
                    if hasattr(pubsub, "aclose"):
                        await pubsub.aclose()
                    else:
                        await pubsub.close()
                except Exception:
                    pass
            if redis:
                try:
                    await redis.aclose()
                except Exception:
                    pass
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break


def _get_http_session() -> aiohttp.ClientSession:
    """Return a shared aiohttp client session for Meta API requests."""
    global _http_session, _clients
    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    session_loop = getattr(_http_session, "_loop", None) if _http_session else None
    if (
        _http_session is None
        or _http_session.closed
        or (loop and session_loop and session_loop != loop)
    ):
        if _http_session and not _http_session.closed:
            try:
                if session_loop and session_loop.is_running():
                    session_loop.create_task(_http_session.close())
                elif loop and loop.is_running():
                    loop.create_task(_http_session.close())
            except Exception:
                pass
        _clients.clear()
        _http_session = aiohttp.ClientSession()
    return _http_session


def _build_whatsapp_ice_servers() -> list[IceServer]:
    """Configure STUN / TURN servers for WhatsApp WebRTC connection."""
    servers: list[IceServer] = []
    if not FORCE_TURN_RELAY:
        servers.append(IceServer(urls="stun:stun.l.google.com:19302"))

    if ENABLE_COTURN and TURN_HOST and TURN_SECRET:
        creds = generate_turn_credentials("whatsapp", TURN_SECRET)
        turn_udp = f"turn:{TURN_HOST}:{TURN_PORT}?transport=udp"
        turn_tcp = f"turn:{TURN_HOST}:{TURN_PORT}?transport=tcp"
        servers.append(
            IceServer(
                urls=[turn_udp, turn_tcp],
                username=creds["username"],
                credential=creds["password"],
            )
        )

    return servers


def _get_or_create_whatsapp_client(
    phone_number_id: str,
    access_token: str,
    app_secret: Optional[str] = None,
) -> WhatsAppClient:
    """Get or instantiate a WhatsAppClient for the given business phone number."""
    client = _clients.get(phone_number_id)
    if not client:
        session = _get_http_session()
        ice_servers = _build_whatsapp_ice_servers()
        client = WhatsAppClient(
            whatsapp_token=access_token,
            phone_number_id=phone_number_id,
            session=session,
            ice_servers=ice_servers,
            whatsapp_secret=app_secret,
        )
        _clients[phone_number_id] = client
    else:
        client.update_whatsapp_token(access_token)
        if app_secret:
            client.update_whatsapp_secret(app_secret)

    return client


async def _reject_whatsapp_call(
    phone_number_id: str,
    call_id: str,
    access_token: Optional[str],
) -> None:
    """Send a call rejection signal to Meta Graph API."""
    if not access_token or not phone_number_id or not call_id:
        return
    try:
        url = f"https://graph.facebook.com/v21.0/{phone_number_id}/calls"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "call_id": call_id,
            "action": "reject",
        }
        session = _get_http_session()
        async with session.post(url, headers=headers, json=payload) as resp:
            logger.info(
                f"[WhatsApp] Call {call_id} rejected via Graph API, status={resp.status}"
            )
    except Exception as e:
        logger.error(f"[WhatsApp] Failed to send call reject to Meta: {e}")


@router.get("/webhook")
async def handle_webhook_verification(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    """Handle Meta's webhook verification handshake (hub.challenge)."""
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Invalid hub.mode")

    # 1. Match against the global environment variable
    if WHATSAPP_WEBHOOK_VERIFY_TOKEN and hub_verify_token == WHATSAPP_WEBHOOK_VERIFY_TOKEN:
        logger.info("[WhatsApp] Webhook verification succeeded via environment verify token")
        return PlainTextResponse(hub_challenge, status_code=200)

    # 2. Fallback: Match against active WhatsApp telephony configurations in the DB
    try:
        matching_config = await db_client.get_whatsapp_configuration_by_verify_token(
            hub_verify_token
        )
        if matching_config:
            logger.info(
                f"[WhatsApp] Webhook verification succeeded for config {matching_config.id}"
            )
            return PlainTextResponse(hub_challenge, status_code=200)
    except Exception as e:
        logger.warning(f"[WhatsApp] Error during DB verify token lookup: {e}")

    logger.warning("[WhatsApp] Webhook verification failed: token mismatch")
    raise HTTPException(status_code=403, detail="Invalid verification token")


def _verify_whatsapp_signature(
    app_secret: Optional[str],
    raw_body: bytes,
    signature_header: Optional[str],
    phone_number_id: Optional[str] = None,
) -> None:
    """Validate Meta's X-Hub-Signature-256 HMAC for incoming webhook payloads.

    Raises:
        HTTPException: 403 if signature is missing, invalid format, app_secret is not configured, or signature mismatch.
    """
    if not app_secret:
        logger.error(
            f"[WhatsApp] Webhook signature verification failed: app_secret not configured "
            f"for phone_number_id={phone_number_id}"
        )
        raise HTTPException(
            status_code=403,
            detail="Webhook signature verification failed: app_secret not configured",
        )

    if not signature_header:
        logger.error(
            f"[WhatsApp] Missing x-hub-signature-256 header for phone_number_id={phone_number_id}"
        )
        raise HTTPException(status_code=403, detail="Missing webhook signature")

    if not signature_header.startswith("sha256="):
        logger.error(f"[WhatsApp] Invalid signature header format: {signature_header}")
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    expected_sig = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    received_sig = signature_header[7:]

    if not hmac.compare_digest(expected_sig, received_sig):
        logger.error(
            f"[WhatsApp] Webhook signature verification failed for phone_number_id={phone_number_id}"
        )
        raise HTTPException(status_code=403, detail="Invalid webhook signature")


@router.post("/webhook")
async def handle_whatsapp_webhook(request: Request):
    """Handle incoming WhatsApp Cloud API webhooks (including calling events)."""
    try:
        raw_body = await request.body()
        signature_header = request.headers.get("x-hub-signature-256")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            logger.error(f"[WhatsApp] Webhook JSON parse error: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        entry_list = payload.get("entry") or []
        if not entry_list:
            return {"status": "success"}

        for entry in entry_list:
            changes = entry.get("changes") or []
            for change in changes:
                field = change.get("field")
                if field != "calls":
                    logger.debug(f"[WhatsApp] Skipping non-calling webhook field: {field}")
                    continue

                value = change.get("value") or {}
                metadata = value.get("metadata") or {}
                phone_number_id = str(metadata.get("phone_number_id") or "")
                calls = value.get("calls") or []
                if not calls:
                    continue

                # Fallback to active connection if phone_number_id is omitted from metadata
                if not phone_number_id:
                    for c in calls:
                        cid = c.get("id")
                        if (
                            cid
                            and cid in _active_connections
                            and len(_active_connections[cid]) > 3
                        ):
                            phone_number_id = str(_active_connections[cid][3])
                            break

                if not phone_number_id:
                    logger.error("[WhatsApp] Webhook calling change missing phone_number_id")
                    raise HTTPException(
                        status_code=400,
                        detail="Missing phone_number_id in webhook metadata",
                    )

                # Look up active configuration for this phone_number_id
                config = await db_client.get_whatsapp_configuration_by_phone_number_id(
                    phone_number_id
                )

                if not config:
                    logger.error(
                        f"[WhatsApp] No active configuration found for phone_number_id {phone_number_id}"
                    )
                    raise HTTPException(
                        status_code=404,
                        detail=f"No active configuration for phone_number_id {phone_number_id}",
                    )

                # Require a valid HMAC-SHA256 signature BEFORE dispatching any calling event
                app_secret = (config.credentials or {}).get("app_secret")
                _verify_whatsapp_signature(
                    app_secret=app_secret,
                    raw_body=raw_body,
                    signature_header=signature_header,
                    phone_number_id=phone_number_id,
                )

                for call_data in calls:
                    event = call_data.get("event")
                    call_id = call_data.get("id")

                    if event == "connect":
                        await _handle_inbound_call_connect(
                            call_data=call_data,
                            metadata=metadata,
                            phone_number_id=phone_number_id,
                            raw_body=raw_body,
                            signature_header=signature_header,
                            payload=payload,
                            config=config,
                        )
                    elif event == "terminate":
                        await _handle_call_terminate(
                            call_data=call_data,
                            payload=payload,
                        )
                    else:
                        logger.info(
                            f"[WhatsApp] Received call event {event!r} for call {call_id}"
                        )

        return {"status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[WhatsApp] Error processing webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Webhook processing failed: {str(e)}"
        )


async def _handle_inbound_call_connect(
    call_data: Dict[str, Any],
    metadata: Dict[str, Any],
    phone_number_id: str,
    raw_body: bytes,
    signature_header: Optional[str],
    payload: Dict[str, Any],
    config: Optional[TelephonyConfigurationModel] = None,
) -> None:
    """Process an incoming WhatsApp call connect event (SDP offer)."""
    call_id = call_data.get("id") or ""
    from_number = call_data.get("from") or ""
    to_number = call_data.get("to") or metadata.get("display_phone_number") or ""
    session_data = call_data.get("session") or {}
    sdp_type = session_data.get("sdp_type")

    logger.info(
        f"[WhatsApp] Incoming call {call_id} from {from_number} to {to_number}, "
        f"sdp_type={sdp_type}"
    )

    if sdp_type != "offer":
        logger.warning(
            f"[WhatsApp] Unexpected sdp_type {sdp_type!r} for inbound call {call_id}"
        )
        return

    # 1. Check for duplicate or active call
    if not call_id:
        logger.warning("[WhatsApp] Missing call_id in connect event")
        return

    if call_id in _active_connections:
        logger.warning(
            f"[WhatsApp] Call {call_id} already active in-memory, ignoring duplicate connect"
        )
        return

    existing_run = await db_client.get_workflow_run_by_call_id(call_id)
    if existing_run:
        logger.warning(
            f"[WhatsApp] Call {call_id} already has workflow_run {existing_run.id}, ignoring duplicate connect"
        )
        return

    # 2. Lookup the TelephonyConfiguration for this phone_number_id if not supplied
    if config is None:
        config = await db_client.get_whatsapp_configuration_by_phone_number_id(
            phone_number_id
        )
        if not config:
            logger.error(
                f"[WhatsApp] No active configuration found for phone_number_id {phone_number_id}"
            )
            return

        app_secret = (config.credentials or {}).get("app_secret")
        _verify_whatsapp_signature(
            app_secret=app_secret,
            raw_body=raw_body,
            signature_header=signature_header,
            phone_number_id=phone_number_id,
        )

    access_token = (config.credentials or {}).get("access_token")

    # 3. Resolve destination phone number & assigned inbound workflow
    normalized_to = (
        normalize_telephony_address(to_number).canonical if to_number else ""
    )
    normalized_from = (
        normalize_telephony_address(from_number).canonical if from_number else ""
    )

    phones = await db_client.list_phone_numbers_for_config(config.id)
    phone_row: Optional[TelephonyPhoneNumberModel] = None
    for p in phones:
        if p.is_active and p.address_normalized == normalized_to:
            phone_row = p
            break

    # Fallback: only when destination to_number was omitted/empty in the incoming webhook,
    # resolve by matching phone_number_id in extra_metadata or single active phone row
    if not phone_row and not normalized_to:
        if phone_number_id:
            for p in phones:
                if not getattr(p, "is_active", True):
                    continue
                meta = getattr(p, "extra_metadata", {}) or {}
                if str(meta.get("phone_number_id") or meta.get("meta_phone_number_id") or "") == str(phone_number_id):
                    phone_row = p
                    break

        if not phone_row:
            active_phones = [p for p in phones if getattr(p, "is_active", True)]
            if len(active_phones) == 1:
                phone_row = active_phones[0]

        if phone_row:
            normalized_to = phone_row.address_normalized

    if not phone_row:
        logger.warning(
            f"[WhatsApp] No active configured phone number found matching destination "
            f"{normalized_to} (or phone_number_id {phone_number_id}) for config {config.id}. "
            f"Rejecting call {call_id}."
        )
        await _reject_whatsapp_call(phone_number_id, call_id, access_token)
        return

    if not phone_row.inbound_workflow_id:
        logger.warning(
            f"[WhatsApp] No inbound workflow assigned for number {to_number} "
            f"(phone_number_id {phone_number_id}). Rejecting call."
        )
        await _reject_whatsapp_call(phone_number_id, call_id, access_token)
        return

    workflow_id = phone_row.inbound_workflow_id
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=config.organization_id
    )
    if not workflow:
        logger.error(
            f"[WhatsApp] Inbound workflow {workflow_id} not found in org {config.organization_id}. Rejecting."
        )
        await _reject_whatsapp_call(phone_number_id, call_id, access_token)
        return

    # 4. Concurrency slot & workflow run creation
    try:
        concurrency_slot = await call_concurrency.acquire_org_slot(
            config.organization_id,
            source="inbound:whatsapp",
            timeout=0,
        )
    except CallConcurrencyLimitError:
        logger.warning(
            f"[WhatsApp] Concurrency limit exceeded for org {config.organization_id}"
        )
        await _reject_whatsapp_call(phone_number_id, call_id, access_token)
        return

    workflow_run = None
    slot_bound = False
    try:
        run_inputs = await prepare_workflow_run_inputs(db_client, workflow)
        workflow_run_name = f"Inbound WhatsApp call from {normalized_from}"

        workflow_run = await db_client.create_workflow_run(
            workflow_run_name,
            workflow_id,
            "whatsapp",
            user_id=workflow.user_id,
            call_type=CallType.INBOUND,
            initial_context={
                "caller_number": normalized_from,
                "called_number": normalized_to,
                "direction": "inbound",
                "provider": "whatsapp",
                "telephony_configuration_id": config.id,
            },
            gathered_context={
                "call_id": call_id,
            },
            logs={
                "inbound_webhook": {
                    "account_id": phone_number_id,
                    "from_number": normalized_from,
                    "to_number": normalized_to,
                    "from_phone_number_id": phone_row.id,
                },
            },
            organization_id=config.organization_id,
            definition_id=run_inputs.definition_id,
        )
        await call_concurrency.bind_workflow_run(concurrency_slot, workflow_run.id)
        slot_bound = True

        quota_result = await authorize_workflow_run_start(
            workflow_id=workflow_id,
            organization_id=config.organization_id,
            workflow_run_id=workflow_run.id,
        )
        if not quota_result.has_quota:
            logger.warning(
                f"[WhatsApp] Org {config.organization_id} has exceeded quota: "
                f"{quota_result.error_message}"
            )
            await mark_workflow_run_failed(
                workflow_run.id, quota_result.error_message or "Quota exceeded"
            )
            await call_concurrency.release_workflow_run_slot(workflow_run.id)
            await _reject_whatsapp_call(phone_number_id, call_id, access_token)
            return

    except Exception as e:
        logger.error(f"[WhatsApp] Failed to initialize workflow run: {e}")
        if workflow_run:
            await mark_workflow_run_failed(workflow_run.id, str(e))
        if slot_bound:
            await call_concurrency.release_workflow_run_slot(workflow_run.id)
        else:
            await call_concurrency.release_slot(concurrency_slot)
        await _reject_whatsapp_call(phone_number_id, call_id, access_token)
        return

    # 5. Connect WebRTC via Pipecat's WhatsAppClient
    # Signature is already verified above in routes.py, so we pass app_secret=None
    # to avoid redundant double-verification in WhatsAppClient.
    client = _get_or_create_whatsapp_client(
        phone_number_id=phone_number_id,
        access_token=access_token,
        app_secret=None,
    )

    async def on_call_connected(
        connection: SmallWebRTCConnection, call: WhatsAppConnectCall
    ) -> None:
        logger.info(
            f"[WhatsApp] Call {call.id} connected! Starting voice pipeline for "
            f"workflow_run {workflow_run.id} in background"
        )
        _active_connections[call.id] = (
            connection,
            workflow_run.id,
            config.organization_id,
            phone_number_id,
        )

        # Store call lifecycle state in shared Redis
        try:
            redis = await _get_redis()
            if redis:
                await redis.setex(
                    f"{WHATSAPP_CALL_KEY_PREFIX}{call.id}",
                    3600,
                    json.dumps({
                        "workflow_run_id": workflow_run.id,
                        "organization_id": config.organization_id,
                        "phone_number_id": phone_number_id,
                    }),
                )
        except Exception as e:
            logger.warning(f"[WhatsApp] Failed to store call state in Redis: {e}")

        # Ensure this worker is subscribed to cross-worker shutdown events
        _ensure_redis_subscriber()

        # Launch the voice pipeline asynchronously so the webhook responds immediately
        pipeline_task = asyncio.create_task(
            _run_whatsapp_pipeline(
                connection=connection,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run.id,
                user_id=workflow.user_id,
                organization_id=config.organization_id,
                call_id=call.id,
            )
        )
        _background_tasks.add(pipeline_task)
        pipeline_task.add_done_callback(_background_tasks.discard)

    try:
        parsed_request = WhatsAppWebhookRequest.model_validate(payload)
        await client.handle_webhook_request(
            request=parsed_request,
            connection_callback=on_call_connected,
            raw_body=raw_body,
            sha256_signature=signature_header,
        )
        logger.info(
            f"[WhatsApp] Successfully accepted call {call_id} and dispatched pipeline"
        )
    except Exception as e:
        logger.error(f"[WhatsApp] Failed to establish WebRTC connection for call {call_id}: {e}")
        await mark_workflow_run_failed(
            workflow_run.id, f"WebRTC connection failed: {e}"
        )
        await call_concurrency.release_workflow_run_slot(workflow_run.id)
        await _reject_whatsapp_call(phone_number_id, call_id, access_token)


async def _run_whatsapp_pipeline(
    connection: SmallWebRTCConnection,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
    organization_id: int,
    call_id: str,
) -> None:
    """Execute Dograh's WebRTC AI pipeline over the active peer connection."""
    try:
        logger.info(
            f"[WhatsApp] Pipeline started for workflow_run {workflow_run_id}, call_id={call_id}"
        )
        await run_pipeline_smallwebrtc(
            webrtc_connection=connection,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            organization_id=organization_id,
        )
        logger.info(
            f"[WhatsApp] Pipeline finished cleanly for workflow_run {workflow_run_id}"
        )
    except Exception as e:
        logger.error(
            f"[WhatsApp] Pipeline error for workflow_run {workflow_run_id}: {e}",
            exc_info=True,
        )
        try:
            await mark_workflow_run_failed(
                workflow_run_id, str(e), only_if_incomplete=True
            )
        except Exception as mark_err:
            logger.warning(
                f"[WhatsApp] Failed to mark workflow run {workflow_run_id} failed: {mark_err}"
            )
    finally:
        _active_connections.pop(call_id, None)
        try:
            redis = await _get_redis()
            if redis:
                await redis.delete(f"{WHATSAPP_CALL_KEY_PREFIX}{call_id}")
        except Exception:
            pass
        try:
            await call_concurrency.release_workflow_run_slot(workflow_run_id)
        except Exception as e:
            logger.warning(
                f"[WhatsApp] Failed to release concurrency slot for workflow_run {workflow_run_id}: {e}"
            )


async def _handle_call_terminate(
    call_data: Dict[str, Any], payload: Dict[str, Any]
) -> None:
    """Handle a WhatsApp call termination event from Meta."""
    call_id = call_data.get("id") or ""
    if not call_id:
        return
    logger.info(f"[WhatsApp] Processing terminate event for call {call_id}")

    # Broadcast terminate to owning worker via Redis pub/sub and delete call key
    try:
        redis = await _get_redis()
        if redis:
            await redis.delete(f"{WHATSAPP_CALL_KEY_PREFIX}{call_id}")
            await redis.publish(
                REDIS_TERMINATE_CHANNEL,
                json.dumps({"call_id": call_id}),
            )
    except Exception as e:
        logger.warning(f"[WhatsApp] Failed to publish terminate to Redis: {e}")

    entry = _active_connections.pop(call_id, None)
    if entry:
        connection, workflow_run_id, org_id = entry[:3]
        try:
            await connection.disconnect()
            logger.info(f"[WhatsApp] Peer connection closed for call {call_id}")
        except Exception as e:
            logger.warning(f"[WhatsApp] Error during peer connection disconnect: {e}")

        # Mark workflow run state if still running
        try:
            await db_client.update_workflow_run(
                workflow_run_id,
                is_completed=True,
                state=WorkflowRunState.COMPLETED.value,
            )
        except Exception as e:
            logger.warning(f"[WhatsApp] Failed to update workflow run on termination: {e}")

        try:
            await call_concurrency.release_workflow_run_slot(workflow_run_id)
        except Exception as e:
            logger.warning(
                f"[WhatsApp] Failed to release concurrency slot on terminate for {workflow_run_id}: {e}"
            )
    else:
        # Cross-worker termination cleanup: when webhook hits a different worker instance
        try:
            run = await db_client.get_workflow_run_by_call_id(call_id)
            if run and not run.is_completed:
                logger.info(
                    f"[WhatsApp] Cleaning up cross-worker workflow run {run.id} for terminated call {call_id}"
                )
                await db_client.update_workflow_run(
                    run.id,
                    is_completed=True,
                    state=WorkflowRunState.COMPLETED.value,
                )
                await call_concurrency.release_workflow_run_slot(run.id)
        except Exception as e:
            logger.warning(
                f"[WhatsApp] Failed cross-worker terminate cleanup for call {call_id}: {e}"
            )


@router.post("/permissions")
async def handle_permission_callback(request: Request):
    """Handle permission callback webhooks from Meta for business-initiated calls."""
    try:
        payload = await request.json()
        logger.info(f"[WhatsApp] Received permission callback: {payload}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[WhatsApp] Error processing permission callback: {e}")
        raise HTTPException(
            status_code=500, detail=f"Permission callback processing failed: {str(e)}"
        )
