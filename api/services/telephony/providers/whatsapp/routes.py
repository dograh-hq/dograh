"""WhatsApp webhook routes.

Meta's WhatsApp Business Calling API announces calls here: the GET handshake
that verifies the webhook, then ``connect`` events carrying an SDP offer and
``terminate`` events when the caller hangs up. Pipecat's ``WhatsAppClient``
answers a ``connect`` over WebRTC and closes the peer connection on
``terminate``; the call itself runs through Dograh's WebRTC voice pipeline.

Meta posts every event to one URL, so with several API workers a call's
``terminate`` can land on a worker that does not hold its peer connection.
Terminates are therefore re-broadcast over Redis, and every worker hands them
to its own client, which closes the calls it holds and ignores the rest.
"""

import asyncio
import json
import uuid
from typing import Any, Dict, Optional

import aiohttp
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.whatsapp.api import (
    WhatsAppApi,
    WhatsAppConnectCall,
    WhatsAppWebhookRequest,
)
from pipecat.transports.whatsapp.client import WhatsAppClient
from starlette.responses import PlainTextResponse

from api.constants import ENABLE_COTURN, FORCE_TURN_RELAY, REDIS_URL
from api.db import db_client
from api.db.models import TelephonyConfigurationModel, TelephonyPhoneNumberModel
from api.enums import CallType
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

from .provider import signature_matches

router = APIRouter(prefix="/whatsapp")

REDIS_TERMINATE_CHANNEL = "whatsapp:call:terminate"

# Marks this worker's own terminate broadcasts so it does not handle them twice.
_WORKER_ID = uuid.uuid4().hex

_background_tasks: set[asyncio.Task] = set()

# Calls whose pipeline runs on this worker, and which of those Meta has
# already ended — so the pipeline's teardown knows whether to end the call.
_owned_calls: set[str] = set()
_terminated_by_meta: set[str] = set()

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


# How long a connect claim outlives the delivery that won it. Meta retries a
# connect it believes unanswered within seconds; long before this lapses the
# run exists and the database check catches any straggler.
CALL_CLAIM_TTL_SECONDS = 600


async def _claim_call(call_id: str) -> bool:
    """Claim a connect atomically across workers; the first delivery wins.

    Meta delivers connect at least once, so two deliveries of one call can
    overlap before either has created its run. The database check alone then
    lets both through and the call is answered twice. Without Redis the claim
    is granted and that check is the only guard.
    """
    try:
        redis = await _get_redis()
        if redis is None:
            return True
        return bool(
            await redis.set(
                _claim_key(call_id), _WORKER_ID, nx=True, ex=CALL_CLAIM_TTL_SECONDS
            )
        )
    except Exception as e:
        logger.warning(f"[WhatsApp] Call claim unavailable for {call_id}: {e}")
        return True


def _claim_key(call_id: str) -> str:
    return f"whatsapp:call:{call_id}:claim"


# Deletes the claim only while this worker still holds it.
_RELEASE_CLAIM_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) end return 0"
)


async def _release_claim(call_id: str) -> None:
    """Hand a claim back so Meta's retry can be answered; only the holder may.

    Used when connect processing failed before the call was answered or
    rejected. Keeping the claim would make the retry report success while
    nobody picks up.
    """
    try:
        redis = await _get_redis()
        if redis is None:
            return
        await redis.eval(_RELEASE_CLAIM_SCRIPT, 1, _claim_key(call_id), _WORKER_ID)
    except Exception as e:
        logger.warning(f"[WhatsApp] Could not release claim for {call_id}: {e}")


def _ensure_redis_subscriber() -> None:
    global _redis_subscriber_task
    if _redis_subscriber_task is None or _redis_subscriber_task.done():
        _redis_subscriber_task = asyncio.create_task(_listen_for_remote_terminates())


async def _listen_for_remote_terminates() -> None:
    """Hand terminate webhooks received by other workers to this worker's clients."""
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
                    if data.get("worker_id") == _WORKER_ID:
                        continue
                    await _dispatch_terminate(
                        phone_number_id=str(data.get("phone_number_id") or ""),
                        payload=data.get("payload") or {},
                        call_id=str(data.get("call_id") or ""),
                    )
                except Exception as e:
                    logger.warning(
                        f"[WhatsApp] Error handling cross-worker terminate message: {e}"
                    )
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
        creds = generate_turn_credentials("whatsapp")
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
        # TURN credentials are time-limited, so a client cached across calls
        # must not keep the ICE servers it was created with. Only new
        # connections pick these up; calls in progress are unaffected.
        client.update_ice_servers(_build_whatsapp_ice_servers())

    return client


def _graph_api(phone_number_id: str, access_token: str) -> WhatsAppApi:
    """Pipecat's Graph API wrapper for one business number, on the shared session."""
    return WhatsAppApi(
        whatsapp_token=access_token,
        phone_number_id=phone_number_id,
        session=_get_http_session(),
    )


async def _reject_whatsapp_call(
    phone_number_id: str,
    call_id: str,
    access_token: Optional[str],
) -> None:
    """Decline a ringing call; the caller sees it rejected."""
    if not access_token or not phone_number_id or not call_id:
        return
    try:
        result = await _graph_api(
            phone_number_id, access_token
        ).reject_call_to_whatsapp(call_id)
        logger.info(f"[WhatsApp] Rejected call {call_id}: {result}")
    except Exception as e:
        logger.error(f"[WhatsApp] Failed to reject call {call_id}: {e}")


async def _terminate_whatsapp_call(
    phone_number_id: str,
    call_id: str,
    access_token: Optional[str],
) -> None:
    """End an accepted call on Meta's side; the caller hears it hang up."""
    if not access_token or not phone_number_id or not call_id:
        return
    try:
        result = await _graph_api(
            phone_number_id, access_token
        ).terminate_call_to_whatsapp(call_id)
        logger.info(f"[WhatsApp] Terminated call {call_id}: {result}")
    except Exception as e:
        logger.error(f"[WhatsApp] Failed to terminate call {call_id}: {e}")


async def _dispatch_terminate(
    phone_number_id: str, payload: Dict[str, Any], call_id: str
) -> None:
    """Give one call's terminate webhook to this worker's client for the number.

    The client closes the peer connection it holds for that call, which ends
    its pipeline, and ignores a call it never answered. A worker with no
    client for the number has nothing to close.
    """
    if call_id in _owned_calls:
        _terminated_by_meta.add(call_id)

    client = _clients.get(phone_number_id)
    if client is None:
        return
    try:
        await client.handle_webhook_request(
            WhatsAppWebhookRequest.model_validate(payload)
        )
    except Exception as e:
        logger.warning(f"[WhatsApp] Terminate handling failed for call {call_id}: {e}")


@router.get("/webhook")
async def handle_webhook_verification(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    """Handle Meta's webhook verification handshake (hub.challenge).

    Every organization's configuration points Meta at this one URL, so the
    token is what identifies which configuration is being verified.
    """
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Invalid hub.mode")

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

    if not signature_matches(app_secret, raw_body, signature_header):
        logger.error(
            f"[WhatsApp] Webhook signature verification failed for phone_number_id={phone_number_id}"
        )
        raise HTTPException(status_code=403, detail="Invalid webhook signature")


def _payload_for_call(
    payload: Dict[str, Any],
    entry: Dict[str, Any],
    change: Dict[str, Any],
    call_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Meta's webhook payload narrowed to a single call.

    Pipecat's client acts on the first connect or terminate it finds and
    returns, so a webhook that batches several calls is handed over one call
    at a time.
    """
    value = {**(change.get("value") or {}), "calls": [call_data]}
    return {**payload, "entry": [{**entry, "changes": [{**change, "value": value}]}]}


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
            # The Business Account the events belong to; the configuration is
            # keyed on it. Each call then names its number by phone_number_id.
            waba_id = str(entry.get("id") or "")
            changes = entry.get("changes") or []
            for change in changes:
                field = change.get("field")
                if field != "calls":
                    logger.debug(
                        f"[WhatsApp] Skipping non-calling webhook field: {field}"
                    )
                    continue

                value = change.get("value") or {}
                metadata = value.get("metadata") or {}
                phone_number_id = str(metadata.get("phone_number_id") or "")
                calls = value.get("calls") or []
                if not calls:
                    continue

                if not phone_number_id:
                    logger.error(
                        "[WhatsApp] Webhook calling change missing phone_number_id"
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="Missing phone_number_id in webhook metadata",
                    )
                if not waba_id:
                    logger.error(
                        "[WhatsApp] Webhook entry missing the WhatsApp Business "
                        "Account ID"
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="Missing WhatsApp Business Account ID in webhook entry",
                    )

                config = await db_client.get_whatsapp_configuration_by_waba_id(waba_id)

                if not config:
                    logger.error(
                        f"[WhatsApp] No active configuration found for WhatsApp "
                        f"Business Account {waba_id}"
                    )
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"No active configuration for WhatsApp Business "
                            f"Account {waba_id}"
                        ),
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
                    call_payload = _payload_for_call(payload, entry, change, call_data)

                    if event == "connect":
                        await _handle_inbound_call_connect(
                            call_data=call_data,
                            metadata=metadata,
                            phone_number_id=phone_number_id,
                            payload=call_payload,
                            config=config,
                        )
                    elif event == "terminate":
                        if call_id:
                            await _handle_call_terminate(
                                phone_number_id, call_payload, call_id
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
    payload: Dict[str, Any],
    config: TelephonyConfigurationModel,
) -> None:
    """Process an incoming WhatsApp call connect event (SDP offer).

    The caller has already matched ``config`` to the Business Account and
    verified the payload signature against its app secret.
    """
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

    if not await _claim_call(call_id):
        logger.warning(
            f"[WhatsApp] Call {call_id} is already being answered, ignoring duplicate connect"
        )
        return

    try:
        await _answer_claimed_call(
            call_id=call_id,
            from_number=from_number,
            to_number=to_number,
            phone_number_id=phone_number_id,
            payload=payload,
            config=config,
        )
    except Exception:
        # The call was neither answered nor rejected, so Meta's retry must be
        # able to try again rather than be swallowed by the claim.
        await _release_claim(call_id)
        raise


async def _answer_claimed_call(
    call_id: str,
    from_number: str,
    to_number: str,
    phone_number_id: str,
    payload: Dict[str, Any],
    config: TelephonyConfigurationModel,
) -> None:
    """Create the run for a claimed connect and hand the call to pipecat.

    Every failure this function handles itself ends with the call rejected on
    Meta's side, so the claim stays. Anything that escapes left the call
    untouched.
    """
    existing_run = await db_client.get_workflow_run_by_call_id(call_id)
    if existing_run:
        logger.warning(
            f"[WhatsApp] Call {call_id} already has workflow_run {existing_run.id}, ignoring duplicate connect"
        )
        return

    access_token = (config.credentials or {}).get("access_token")

    # 2. Resolve the called number on this configuration & its inbound workflow
    normalized_to = (
        normalize_telephony_address(to_number).canonical if to_number else ""
    )
    normalized_from = (
        normalize_telephony_address(from_number).canonical if from_number else ""
    )

    phones = await db_client.list_phone_numbers_for_config(config.id)
    phone_row: Optional[TelephonyPhoneNumberModel] = next(
        (p for p in phones if p.is_active and p.address_normalized == normalized_to),
        None,
    )

    if not phone_row:
        logger.warning(
            f"[WhatsApp] No active phone number {normalized_to!r} on config "
            f"{config.id} (phone_number_id {phone_number_id}). Rejecting call {call_id}."
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

    # 3. Concurrency slot & workflow run creation
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

    # 4. Connect WebRTC via Pipecat's WhatsAppClient
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
        _owned_calls.add(call.id)

        # Ensure this worker hears terminates that reach other workers
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
                phone_number_id=phone_number_id,
                access_token=access_token,
            )
        )
        _background_tasks.add(pipeline_task)
        pipeline_task.add_done_callback(_background_tasks.discard)

    try:
        await client.handle_webhook_request(
            request=WhatsAppWebhookRequest.model_validate(payload),
            connection_callback=on_call_connected,
        )
        logger.info(
            f"[WhatsApp] Successfully accepted call {call_id} and dispatched pipeline"
        )
    except Exception as e:
        logger.error(
            f"[WhatsApp] Failed to establish WebRTC connection for call {call_id}: {e}"
        )
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
    phone_number_id: str,
    access_token: Optional[str],
) -> None:
    """Execute Dograh's WebRTC AI pipeline over the active peer connection.

    The pipeline ends either because Meta closed the call (the client dropped
    the peer connection on ``terminate``) or on its own — the agent hung up or
    the connection died. Only in the second case is Meta still holding the
    call open for the caller, so only then is it told to end it.
    """
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
        # Best-effort by contract; a run the pipeline already completed is left
        # as it is, so a teardown error cannot rewrite a finished call.
        try:
            run = await db_client.get_workflow_run(
                workflow_run_id, organization_id=organization_id
            )
            if run and not run.is_completed:
                await mark_workflow_run_failed(workflow_run_id, str(e))
        except Exception as mark_error:
            logger.warning(
                f"[WhatsApp] Could not mark workflow_run {workflow_run_id} failed: {mark_error}"
            )
    finally:
        _owned_calls.discard(call_id)
        if call_id in _terminated_by_meta:
            _terminated_by_meta.discard(call_id)
        else:
            await _terminate_whatsapp_call(phone_number_id, call_id, access_token)
        try:
            await call_concurrency.release_workflow_run_slot(workflow_run_id)
        except Exception as e:
            logger.warning(
                f"[WhatsApp] Failed to release concurrency slot for workflow_run {workflow_run_id}: {e}"
            )


async def _handle_call_terminate(
    phone_number_id: str, payload: Dict[str, Any], call_id: str
) -> None:
    """Close a call Meta reports ended, on whichever worker holds it.

    This worker tries first; the broadcast reaches the others. The pipeline
    behind a closed connection winds down on its own and completes the run,
    so nothing is written to the run here.
    """
    logger.info(f"[WhatsApp] Processing terminate for call {call_id}")
    await _dispatch_terminate(phone_number_id, payload, call_id)

    try:
        redis = await _get_redis()
        if redis:
            await redis.publish(
                REDIS_TERMINATE_CHANNEL,
                json.dumps(
                    {
                        "worker_id": _WORKER_ID,
                        "phone_number_id": phone_number_id,
                        "call_id": call_id,
                        "payload": payload,
                    }
                ),
            )
    except Exception as e:
        logger.warning(f"[WhatsApp] Failed to publish terminate to Redis: {e}")
