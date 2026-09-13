"""WhatsApp telephony service layer.

Manages active WebRTC connections, client caches, Redis pub/sub for
cross-worker events, and ICE/TURN credentials. Decouples core provider logic
from HTTP router handlers.
"""

import asyncio
import json
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import aiohttp
import redis.asyncio as aioredis
from loguru import logger
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.whatsapp.client import WhatsAppClient

from api.constants import (
    ENABLE_COTURN,
    FORCE_TURN_RELAY,
    REDIS_URL,
    TURN_HOST,
    TURN_PORT,
    TURN_SECRET,
)
from api.db import db_client
from api.enums import WorkflowRunState
from api.services.call_concurrency import call_concurrency
from api.services.pipecat.call_gate import ANSWERED, TERMINATED, OutboundCallGate
from api.services.turn import generate_turn_credentials
from api.services.workflow_run_failure import mark_workflow_run_failed

# Redis Channels & Key Prefixes
REDIS_TERMINATE_CHANNEL = "whatsapp:call:terminate"
REDIS_CALL_EVENTS_CHANNEL = "whatsapp:call:events"
REDIS_PERMISSION_CHANNEL = "whatsapp:permission:updated"
WHATSAPP_CALL_KEY_PREFIX = "whatsapp:call:"

# Active in-memory registry of ongoing WhatsApp WebRTC calls on this worker:
# call_id -> (SmallWebRTCConnection, workflow_run_id, organization_id, phone_number_id)
_active_connections: Dict[str, Tuple[SmallWebRTCConnection, int, int, str]] = {}
_outbound_answered_events: Dict[str, OutboundCallGate] = {}
# call_id -> its pipeline task, so a rollback can cancel exactly the right one
# instead of diffing the background-task set from outside this module.
_pipeline_tasks: Dict[str, asyncio.Task] = {}
# call_id -> the pipeline task some other teardown has cancelled and taken
# ownership of. That task's finalizer ends the call too - it runs
# terminate_whatsapp_call_by_id, which re-enters handle_call_terminate - so
# without this a termination starting outside the pipeline (Meta webhook,
# cross-worker publish, provider end_call, outbound rollback) makes the
# pipeline it just cancelled issue a second Graph API terminate, a second
# terminate publish and a second completion write for the same call.
#
# Keyed by the task, and cleared by that task's own done callback, because the
# canceller's wait is bounded: a pipeline that ignores cancellation for longer
# than the timeout is abandoned, runs its finalizer later, and must still be
# suppressed then. Tying the entry to the task's lifetime rather than to how
# long anyone waited is what makes that case safe, and it still lets a genuine
# later termination of the same call through - by then the task is done and
# the entry is gone.
_pipeline_teardown_claims: Dict[str, asyncio.Task] = {}


def _claim_pipeline_teardown(call_id: str, task: asyncio.Task) -> None:
    """Take ownership of ``task``'s teardown before cancelling it."""
    _pipeline_teardown_claims[call_id] = task
    task.add_done_callback(
        lambda finished, cid=call_id: (
            _pipeline_teardown_claims.pop(cid, None)
            if _pipeline_teardown_claims.get(cid) is finished
            else None
        )
    )


def _teardown_claimed_by_other(call_id: str) -> bool:
    """True when the current task is a pipeline whose teardown someone else owns."""
    return _pipeline_teardown_claims.get(call_id) is asyncio.current_task()
_background_tasks: Set[asyncio.Task] = set()

# Reusable aiohttp session and cached clients per phone_number_id
_http_session: Optional[aiohttp.ClientSession] = None
_clients: Dict[str, WhatsAppClient] = {}

# Redis client and subscriber
_redis_client: Optional[aioredis.Redis] = None
_redis_subscriber_task: Optional[asyncio.Task] = None

async def _run_whatsapp_pipeline(
    connection: SmallWebRTCConnection,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
    organization_id: int,
    call_id: str,
    call_answered_event: Optional["OutboundCallGate"] = None,
) -> None:
    try:
        from api.services.pipecat.run_pipeline import run_pipeline_smallwebrtc

        logger.info(
            f"[WhatsApp] Pipeline started for workflow_run {workflow_run_id}, call_id={call_id}"
        )
        await run_pipeline_smallwebrtc(
            webrtc_connection=connection,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            organization_id=organization_id,
            call_answered_event=call_answered_event,
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
        answered_evt = _outbound_answered_events.pop(call_id, None)
        if answered_evt:
            answered_evt.resolve(TERMINATED)
        provider_terminated = False
        # Cleared by this task's done callback, not here: reading it must not
        # depend on finishing before whoever cancelled us gave up waiting.
        if _teardown_claimed_by_other(call_id):
            # handle_call_terminate cancelled this task and owns the whole
            # teardown - Meta terminate, the active-connection entry, the peer
            # disconnect, the run completion and the concurrency slot. Return
            # rather than fall through: the steps below are individually
            # idempotent today, but running them twice for one call only
            # stays harmless for as long as every one of them stays that way.
            logger.debug(
                f"[WhatsApp] Pipeline cleanup for call {call_id} deferring to the "
                "teardown that cancelled it"
            )
            return

        try:
            provider_terminated = await terminate_whatsapp_call_by_id(
                call_id=call_id,
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
            )
        except Exception as term_err:
            logger.warning(
                f"[WhatsApp] Failed to send terminate in pipeline cleanup for call {call_id}: {term_err}"
            )
        _active_connections.pop(call_id, None)
        # Only clear the Redis recovery key once Meta has actually confirmed the
        # hangup. terminate_whatsapp_call_by_id -> _handle_call_terminate already
        # leaves the key in place when termination is unconfirmed so a retry can
        # recover phone_number_id/organization_id for call_id; an unconditional
        # delete here would undo that preservation immediately afterwards.
        if provider_terminated:
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


# Pipeline runner hook defaulted to the WhatsApp voice pipeline runner
_pipeline_runner: Optional[Callable[..., Any]] = _run_whatsapp_pipeline


def set_pipeline_runner(runner: Callable[..., Any]) -> None:
    """Register the voice pipeline runner callable."""
    global _pipeline_runner
    _pipeline_runner = runner


def get_pipeline_runner() -> Optional[Callable[..., Any]]:
    """Retrieve the registered voice pipeline runner callable."""
    return _pipeline_runner


def resolve_pipeline_runner() -> Optional[Callable[..., Any]]:
    """Retrieve the registered voice pipeline runner callable."""
    return _pipeline_runner


def get_http_session() -> aiohttp.ClientSession:
    """Retrieve or create an aiohttp ClientSession bound to the running event loop."""
    global _http_session, _clients
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

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


def build_whatsapp_ice_servers() -> List[IceServer]:
    """Configure STUN / TURN servers for WhatsApp WebRTC connections.

    The TURN credentials this mints are time-limited (TURN_CREDENTIAL_TTL), so
    the result is only good for as long as that TTL - call it per client fetch
    rather than once per process.
    """
    servers: List[IceServer] = []
    if not FORCE_TURN_RELAY:
        servers.append(IceServer(urls="stun:stun.l.google.com:19302"))

    if ENABLE_COTURN and TURN_HOST and TURN_SECRET:
        # Second argument is the TTL, not the secret - the generator reads
        # TURN_SECRET from constants and adds this to the current time. Passing
        # the secret here made that an int + str and raised on every ICE build
        # with coturn enabled.
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


def get_or_create_whatsapp_client(
    phone_number_id: str,
    access_token: str,
    app_secret: Optional[str] = None,
) -> WhatsAppClient:
    """Get or instantiate a WhatsAppClient for the given business phone number."""
    client = _clients.get(phone_number_id)
    if not client:
        session = get_http_session()
        client = WhatsAppClient(
            whatsapp_token=access_token,
            phone_number_id=phone_number_id,
            session=session,
            ice_servers=build_whatsapp_ice_servers(),
            whatsapp_secret=app_secret,
        )
        _clients[phone_number_id] = client
    else:
        client.update_whatsapp_token(access_token)
        if app_secret:
            client.update_whatsapp_secret(app_secret)
        # Every new call builds its peer connection from the client's stored ICE
        # list, and the TURN credentials in it expire. A worker outliving
        # TURN_CREDENTIAL_TTL would otherwise hand every subsequent call the same
        # dead credentials and lose TURN relay.
        client.update_ice_servers(build_whatsapp_ice_servers())

    return client


async def get_whatsapp_redis() -> Optional[aioredis.Redis]:
    """Get or instantiate the Redis client for WhatsApp state and pub/sub."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning(f"[WhatsApp] Failed to connect to Redis: {e}")
            return None
    return _redis_client


def ensure_redis_subscriber() -> None:
    """Ensure background listener for cross-worker events is running."""
    global _redis_subscriber_task
    if _redis_subscriber_task is None or _redis_subscriber_task.done():
        _redis_subscriber_task = asyncio.create_task(listen_for_remote_events())


async def listen_for_remote_events() -> None:
    """Listen for cross-worker events (terminate, accepted SDP) on Redis pub/sub."""
    while True:
        redis = None
        pubsub = None
        try:
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            pubsub = redis.pubsub()
            await pubsub.subscribe(REDIS_TERMINATE_CHANNEL, REDIS_CALL_EVENTS_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    channel = message.get("channel")
                    data = json.loads(message["data"])
                    target_call_id = data.get("call_id")
                    if not target_call_id or target_call_id not in _active_connections:
                        continue

                    if channel == REDIS_TERMINATE_CHANNEL:
                        answered_evt = _outbound_answered_events.pop(target_call_id, None)
                        if answered_evt:
                            answered_evt.resolve(TERMINATED)
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
                    elif channel == REDIS_CALL_EVENTS_CHANNEL:
                        event_type = data.get("event")
                        if event_type in ("sdp_answer", "accepted"):
                            sdp = data.get("sdp")
                            sdp_type = data.get("sdp_type", "answer")
                            if sdp and target_call_id in _active_connections:
                                conn = _active_connections[target_call_id][0]
                                try:
                                    logger.info(
                                        f"[WhatsApp] Applying cross-worker SDP answer for call {target_call_id}"
                                    )
                                    await conn.set_answer(sdp, type=sdp_type)
                                    # Setting the remote description alone never starts
                                    # the peer connection - the local handlers connect
                                    # right after, and this path has to do the same.
                                    if (
                                        not getattr(conn, "_connect_invoked", False)
                                        or conn.is_connected()
                                    ):
                                        await conn.connect()
                                except Exception as e:
                                    logger.warning(
                                        f"[WhatsApp] Failed to set cross-worker answer: {e}"
                                    )
                            if event_type == "accepted":
                                # The webhook worker can only set this event when it
                                # owns the connection, so the owning worker must
                                # unblock its own waiting pipeline here.
                                answered_evt = _outbound_answered_events.get(target_call_id)
                                if answered_evt and not answered_evt.is_set():
                                    logger.info(
                                        f"[WhatsApp] Unblocking pipeline for cross-worker answered call {target_call_id}"
                                    )
                                    answered_evt.resolve(ANSWERED)

                                # The webhook worker's local _handle_call_accepted
                                # only runs its state-update block when it owns the
                                # connection, so on a cross-worker delivery nothing
                                # ever marks the call answered: call_status/
                                # connected_at stay unset here, and the shared
                                # call-status endpoint (resolve_live_call_state
                                # below) reads exactly those fields to report
                                # "answered". Mirror that update on this, the
                                # owning, worker.
                                accept_entry = _active_connections.get(target_call_id)
                                if accept_entry:
                                    accept_conn, accept_run_id = accept_entry[0], accept_entry[1]
                                    if hasattr(accept_conn, "call_status"):
                                        accept_conn.call_status = "in-progress"
                                    now_iso = datetime.now(timezone.utc).isoformat()
                                    if hasattr(accept_conn, "connected_at") and not accept_conn.connected_at:
                                        accept_conn.connected_at = now_iso
                                    try:
                                        run = await db_client.get_workflow_run(accept_run_id)
                                        if run:
                                            ctx = dict(run.gathered_context or {})
                                            ctx["call_status"] = "in-progress"
                                            if not ctx.get("connected_at"):
                                                ctx["connected_at"] = now_iso
                                            await db_client.update_workflow_run(
                                                accept_run_id,
                                                state=WorkflowRunState.RUNNING.value,
                                                gathered_context=ctx,
                                            )
                                    except Exception as e:
                                        logger.warning(
                                            f"[WhatsApp] Failed to update workflow run on "
                                            f"cross-worker accepted for {target_call_id}: {e}"
                                        )
                except Exception as parse_err:
                    logger.warning(f"[WhatsApp] Error handling cross-worker message: {parse_err}")
        except asyncio.CancelledError:
            break
        except Exception as conn_err:
            logger.warning(f"[WhatsApp] Redis subscriber connection error: {conn_err}")
        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.close()
                except Exception:
                    pass
            if redis:
                try:
                    await redis.close()
                except Exception:
                    pass
        await asyncio.sleep(5)


def register_outbound_active_connection(
    call_id: str,
    connection: SmallWebRTCConnection,
    workflow_run_id: int,
    organization_id: int,
    phone_number_id: str,
    workflow_id: int,
    user_id: int,
) -> None:
    """Register an active outbound WebRTC connection and run its voice pipeline."""
    runner = resolve_pipeline_runner()
    if runner is None:
        # Registering without a pipeline yields a connected but silent call that
        # nothing ever answers; fail before the connection is tracked so the
        # caller tears the call down instead.
        raise RuntimeError(
            f"No WhatsApp pipeline runner configured; refusing to register outbound call {call_id}"
        )

    answered_event = OutboundCallGate()
    _outbound_answered_events[call_id] = answered_event

    _active_connections[call_id] = (
        connection,
        workflow_run_id,
        organization_id,
        phone_number_id,
    )
    ensure_redis_subscriber()

    pipeline_task = asyncio.create_task(
        runner(
            connection=connection,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            organization_id=organization_id,
            call_id=call_id,
            call_answered_event=answered_event,
        )
    )
    _background_tasks.add(pipeline_task)
    _pipeline_tasks[call_id] = pipeline_task
    pipeline_task.add_done_callback(_background_tasks.discard)
    pipeline_task.add_done_callback(
        lambda _t, cid=call_id: _pipeline_tasks.pop(cid, None)
    )


async def unregister_outbound_active_connection(
    call_id: str, timeout: float = 5.0
) -> None:
    """Undo everything ``register_outbound_active_connection`` published.

    Registration publishes three things at once - the active-connection entry,
    the answer gate, and a running pipeline task - so a caller that tears down
    only the peer connection leaves a dead call visible to cross-worker
    terminate handling and status polling, with a pipeline still holding an
    LLM/TTS session and a workflow run open for the life of the worker.

    Owning the teardown here is what lets the task be looked up by ``call_id``
    rather than inferred from outside this module. Safe to call when
    registration never happened, or twice.
    """
    _active_connections.pop(call_id, None)

    gate = _outbound_answered_events.pop(call_id, None)
    if gate is not None:
        # Release anything parked on the answer, with the truthful outcome.
        gate.resolve(TERMINATED)

    task = _pipeline_tasks.pop(call_id, None)
    if task is None or task.done():
        return

    # Same claim handle_call_terminate uses: the caller rolling this call back
    # hangs it up at Meta itself, so the cancelled pipeline's finalizer must not
    # issue its own terminate for the same call - including when it gets there
    # after the bounded wait below has given up.
    _claim_pipeline_teardown(call_id, task)
    task.cancel()
    try:
        # Bounded: a pipeline that swallows cancellation must not hang the
        # caller's request.
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        logger.warning(
            f"[WhatsApp] Pipeline task for {call_id} did not stop within {timeout}s "
            "after cancellation; abandoning it."
        )
    except Exception as e:
        logger.warning(f"[WhatsApp] Pipeline task for {call_id} errored during teardown: {e}")


# Aliases for backwards compatibility with existing private names
_get_http_session = get_http_session
_build_whatsapp_ice_servers = build_whatsapp_ice_servers
_get_or_create_whatsapp_client = get_or_create_whatsapp_client
_get_redis = get_whatsapp_redis
_ensure_redis_subscriber = ensure_redis_subscriber
_listen_for_remote_events = listen_for_remote_events


async def handle_call_terminate(
    call_data: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    provider_termination_unconfirmed: bool = False,
) -> None:
    """Handle a WhatsApp call termination event from Meta.

    ``provider_termination_unconfirmed`` marks the case where we are tearing
    down without Meta having acknowledged the hangup. Resources we own (peer
    connection, pipeline, concurrency slot) still come down - leaving a pipeline
    running against a call nobody is watching is strictly worse - but the Redis
    recovery key is left in place so the call can still be identified and the
    hangup re-issued. The durable stamp on the run is written by
    ``terminate_whatsapp_call_by_id``, not here: this block only executes on the
    worker that owns the connection, so a cross-worker terminate would otherwise
    lose it.

    Lives in the service layer (not routes.py) so provider.py's ``end_call`` can
    call it without a call-time import back into the HTTP route module - see
    ``WhatsAppProvider.end_call``.
    """
    call_id = call_data.get("id") or ""
    if not call_id:
        return
    logger.info(f"[WhatsApp] Processing terminate event for call {call_id}")

    # Broadcast terminate to owning worker via Redis pub/sub and delete call key
    try:
        redis = await _get_redis()
        if redis:
            if not provider_termination_unconfirmed:
                await redis.delete(f"{WHATSAPP_CALL_KEY_PREFIX}{call_id}")
            await redis.publish(
                REDIS_TERMINATE_CHANNEL,
                json.dumps({"call_id": call_id}),
            )
    except Exception as e:
        logger.warning(f"[WhatsApp] Failed to publish terminate to Redis: {e}")

    answered_evt = _outbound_answered_events.pop(call_id, None)
    if answered_evt:
        answered_evt.resolve(TERMINATED)

    task = _pipeline_tasks.pop(call_id, None)
    if task is not None and not task.done():
        current = asyncio.current_task()
        if current is not task:
            # Claimed before the cancellation so the task's finalizer, which
            # runs terminate_whatsapp_call_by_id, sees that this teardown owns
            # the call and does not re-enter this function. The claim outlives
            # the wait below - see _claim_pipeline_teardown.
            _claim_pipeline_teardown(call_id, task)
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning(
                    f"[WhatsApp] Pipeline task for {call_id} did not stop within 5.0s "
                    "after cancellation; abandoning it."
                )
            except Exception as e:
                logger.warning(f"[WhatsApp] Pipeline task for {call_id} errored during teardown: {e}")

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
            errors = call_data.get("errors") or []
            err_msg = None
            if errors and isinstance(errors, list) and len(errors) > 0:
                err_msg = errors[0].get("title") or errors[0].get("message")
            run = await db_client.get_workflow_run(workflow_run_id)
            ctx = dict(run.gathered_context or {}) if run else {}
            ctx["call_status"] = "failed" if err_msg else "completed"
            ctx["ended_at"] = datetime.now(timezone.utc).isoformat()
            if err_msg:
                ctx["error"] = err_msg
            await db_client.update_workflow_run(
                workflow_run_id,
                is_completed=True,
                state=WorkflowRunState.COMPLETED.value,
                gathered_context=ctx,
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
                errors = call_data.get("errors") or []
                err_msg = None
                if errors and isinstance(errors, list) and len(errors) > 0:
                    err_msg = errors[0].get("title") or errors[0].get("message")
                ctx = dict(run.gathered_context or {})
                ctx["call_status"] = "failed" if err_msg else "completed"
                ctx["ended_at"] = datetime.now(timezone.utc).isoformat()
                if err_msg:
                    ctx["error"] = err_msg
                await db_client.update_workflow_run(
                    run.id,
                    is_completed=True,
                    state=WorkflowRunState.COMPLETED.value,
                    gathered_context=ctx,
                )
                await call_concurrency.release_workflow_run_slot(run.id)
        except Exception as e:
            logger.warning(
                f"[WhatsApp] Failed cross-worker terminate cleanup for call {call_id}: {e}"
            )


async def terminate_whatsapp_call_by_id(
    call_id: str,
    workflow_run_id: Optional[int] = None,
    organization_id: Optional[int] = None,
) -> bool:
    """Terminate an active WhatsApp call locally and via Meta Graph API."""
    answered_evt = _outbound_answered_events.pop(call_id, None)
    if answered_evt:
        answered_evt.resolve(TERMINATED)

    phone_number_id = None
    entry = _active_connections.get(call_id)
    if entry:
        phone_number_id = entry[3]
        if not workflow_run_id:
            workflow_run_id = entry[1]
    elif call_id:
        try:
            redis = await _get_redis()
            if redis:
                raw_data = await redis.get(f"{WHATSAPP_CALL_KEY_PREFIX}{call_id}")
                if raw_data:
                    data = json.loads(raw_data)
                    phone_number_id = data.get("phone_number_id")
                    if not workflow_run_id:
                        workflow_run_id = data.get("workflow_run_id")
        except Exception as e:
            logger.warning(f"[WhatsApp] Redis lookup error during termination: {e}")

    # Fallback to workflow run gathered_context
    if not phone_number_id and workflow_run_id:
        try:
            run = await db_client.get_workflow_run(workflow_run_id)
            if run:
                ctx = run.gathered_context or {}
                phone_number_id = ctx.get("from_phone_number_id") or ctx.get("phone_number_id")
                if not organization_id:
                    organization_id = run.organization_id
        except Exception:
            pass

    # 1. Ask Meta Graph API to terminate the call on user device.
    # Tracked separately from the local teardown below: if Meta never confirms,
    # the recipient's leg may still be up, and the caller must not be told the
    # hang-up succeeded.
    provider_terminated = False
    try:
        config = None
        if phone_number_id:
            config = await db_client.get_whatsapp_configuration_by_phone_number_id(
                str(phone_number_id)
            )
        if not config and organization_id:
            # Org-scoped: list_active_telephony_configurations_by_provider is
            # cross-org and takes only `provider`, so the previous call both
            # raised TypeError (swallowed by the except below, which is why this
            # fallback never once worked) and would have reached another
            # tenant's credentials if it had.
            configs = await db_client.list_telephony_configurations_by_provider(
                organization_id, "whatsapp"
            )
            config = configs[0] if len(configs) == 1 else None

        if config:
            creds = config.credentials or {}
            access_token = creds.get("access_token") or creds.get("api_key")
            app_secret = creds.get("app_secret")
            config_phone_number_id = str(creds.get("phone_number_id") or phone_number_id or "")
            if access_token and config_phone_number_id:
                client = _get_or_create_whatsapp_client(
                    config_phone_number_id, access_token, app_secret
                )
                if client and client._whatsapp_api:
                    resp = await client._whatsapp_api.terminate_call_to_whatsapp(call_id)
                    provider_terminated = True
                    logger.info(
                        f"[WhatsApp] Sent terminate request to Meta API for call {call_id}: resp={resp}"
                    )
                else:
                    logger.warning(
                        f"[WhatsApp] Cannot terminate call {call_id} at Meta: no API client"
                    )
            else:
                logger.warning(
                    f"[WhatsApp] Cannot terminate call {call_id}: missing access_token or phone_number_id"
                )
        else:
            logger.warning(
                f"[WhatsApp] Cannot terminate call {call_id}: no active WhatsApp configuration found"
            )
    except Exception as e:
        logger.warning(f"[WhatsApp] Meta Graph API termination failed: {e}")

    # 2. Tear down locally. This runs even when Meta did not confirm: dropping
    # our peer usually ends the call anyway, and leaving a pipeline and a
    # concurrency slot held for a call nobody can observe is worse. What we do
    # NOT do in that case is discard the call's identity - see
    # provider_termination_unconfirmed, which keeps the Redis recovery key and
    # stamps the run so the hangup can be re-issued against the same call.
    await handle_call_terminate(
        {"id": call_id},
        {},
        provider_termination_unconfirmed=not provider_terminated,
    )

    if not provider_terminated:
        logger.error(
            f"[WhatsApp] Local teardown for call {call_id} completed, but Meta never "
            "confirmed termination; the recipient's leg may still be connected."
        )

    # Stamp the run here rather than inside the teardown: this runs on whichever
    # worker handled the request, owning the connection or not, so the durable
    # record of an unconfirmed hangup survives a cross-worker terminate. Also
    # clears the stamp when a retry finally succeeds - that retry has no active
    # connection left, so the teardown's run update is skipped entirely.
    if workflow_run_id:
        try:
            run = await db_client.get_workflow_run(workflow_run_id)
            ctx = dict(run.gathered_context or {}) if run else {}
            if ctx.get("provider_termination_unconfirmed") != (not provider_terminated):
                ctx["provider_termination_unconfirmed"] = not provider_terminated
                await db_client.update_workflow_run(
                    workflow_run_id, gathered_context=ctx
                )
        except Exception as e:
            logger.warning(
                f"[WhatsApp] Failed recording termination-confirmation state for "
                f"run {workflow_run_id}: {e}"
            )
    return provider_terminated


# Aliases for backwards compatibility with existing private names used by
# routes.py. routes.py imports and re-exports these under their original
# module-level names (``_handle_call_terminate``, ``terminate_whatsapp_call_by_id``)
# so existing callers and any test that patches ``routes.terminate_whatsapp_call_by_id``
# or ``routes._handle_call_terminate`` keep working unchanged.
_handle_call_terminate = handle_call_terminate


def resolve_live_call_state(call_id, workflow_run_id):
    """Report this worker's in-memory view of a WhatsApp call.

    Pure and synchronous by contract (ProviderSpec.live_call_state_resolver):
    the call-status endpoint polls it once a second, so it touches only the
    active-connection registry. ``answered`` comes from the connection's
    call_status, which Meta's ACCEPTED webhook sets - the peer connects during
    dialling, so being connected is not the same as being answered.
    """
    from api.services.telephony.registry import LiveCallState

    entry = _active_connections.get(call_id) if call_id else None
    if entry is None:
        for cid, candidate in _active_connections.items():
            if candidate[1] == workflow_run_id:
                call_id, entry = cid, candidate
                break
    if entry is None:
        return None

    connection = entry[0]
    try:
        peer_connected = bool(connection.is_connected())
    except Exception:
        peer_connected = False

    return LiveCallState(
        call_id=call_id,
        peer_connected=peer_connected,
        answered=getattr(connection, "call_status", None) == "in-progress",
    )

