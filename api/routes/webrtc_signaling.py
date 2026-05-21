"""WebSocket-based WebRTC signaling endpoint with ICE trickling support.

This implementation uses WebSocket-based signaling instead of HTTP PATCH for ICE candidates,
which is suitable for multi-worker FastAPI deployments where local _pcs_map cannot be shared.

Uses the SmallWebRTC API contract:
- SmallWebRTCConnection for peer connection management
- candidate_from_sdp() for parsing ICE candidates
- add_ice_candidate() for trickling support

TURN Authentication:
- Uses time-limited credentials (TURN REST API) when TURN_SECRET is configured
- Credentials are generated per-connection using HMAC-SHA1
- Falls back to static credentials if TURN_SECRET is not set (legacy mode)
"""

import asyncio
import ipaddress
import os
from datetime import UTC, datetime
from enum import Enum
from typing import Dict, List, Optional

from aiortc import RTCIceServer
from aiortc.sdp import candidate_from_sdp
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.utils.run_context import set_current_org_id, set_current_run_id
from starlette.websockets import WebSocketState

from api.constants import ENVIRONMENT, FORCE_TURN_RELAY
from api.db import db_client
from api.db.models import UserModel
from api.enums import Environment
from api.routes.turn_credentials import (
    TURN_HOST,
    TURN_PORT,
    TURN_SECRET,
    generate_turn_credentials,
)
from api.services.auth.depends import get_user_ws
from api.services.pipecat.run_pipeline import run_pipeline_smallwebrtc
from api.services.pipecat.ws_sender_registry import (
    register_ws_sender,
    unregister_ws_sender,
)
from api.services.quota_service import check_dograh_quota

router = APIRouter(prefix="/ws")


class NonRelayFilterPolicy(Enum):
    """What to filter from non-relay ICE candidates. Relay candidates always pass."""

    NONE = "none"  # filter nothing — pass all candidates
    PRIVATE = "private"  # filter non-relay candidates with private/CGNAT IPs
    ALL = "all"  # filter all non-relay candidates (relay-only mode)


def is_local_or_cgnat_ip(ip_str: str) -> bool:
    """Return True for RFC1918, loopback, link-local, and CGNAT addresses."""

    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    is_cgnat = ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10")
    return ip.is_private or ip.is_loopback or ip.is_link_local or is_cgnat


def resolve_ice_filter_policies(
    environment: str,
    force_turn_relay: bool,
    server_ip: str,
) -> tuple[NonRelayFilterPolicy, NonRelayFilterPolicy]:
    """Resolve outbound and inbound non-relay filtering for this deployment."""

    private_lan_deployment = (
        environment != Environment.LOCAL.value and is_local_or_cgnat_ip(server_ip)
    )

    if force_turn_relay:
        # Relay-only diagnostics stay explicit. On private LAN deployments we
        # must still accept inbound private candidates for relay<->host pairs.
        outbound_policy = NonRelayFilterPolicy.ALL
        inbound_policy = (
            NonRelayFilterPolicy.NONE
            if private_lan_deployment
            else NonRelayFilterPolicy.PRIVATE
        )
        return outbound_policy, inbound_policy

    if environment == Environment.LOCAL.value or private_lan_deployment:
        return NonRelayFilterPolicy.NONE, NonRelayFilterPolicy.NONE

    # Public remote deployment: drop private-IP host candidates to avoid
    # coturn denied-peer-ip errors against Docker bridge and LAN interfaces.
    return NonRelayFilterPolicy.PRIVATE, NonRelayFilterPolicy.PRIVATE


ICE_OUTBOUND_POLICY, ICE_INBOUND_POLICY = resolve_ice_filter_policies(
    ENVIRONMENT,
    FORCE_TURN_RELAY,
    os.getenv("SERVER_IP", ""),
)


def is_private_ip_candidate(candidate_str: str) -> bool:
    """Check if ICE candidate contains a private IP address or CGNAT IP Address.

    Parses the candidate string to extract the IP address and checks if it's private.
    This is used to filter out host candidates with private IPs in non-local environments,
    preventing TURN relay errors when coturn blocks private IP ranges or CGNAT IP Addresses.

    Args:
        candidate_str: ICE candidate string, e.g.,
            "candidate:123 1 udp 2122260223 192.168.50.24 63603 typ host ..."

    Returns:
        True if the candidate contains a private IP, False otherwise.
    """
    try:
        parts = candidate_str.split()
        # Find "typ" and get the IP which is 2 positions before it
        if "typ" in parts:
            typ_index = parts.index("typ")
            ip_str = parts[typ_index - 2]
            return is_local_or_cgnat_ip(ip_str)
    except (ValueError, IndexError):
        pass
    return False


def _keep_candidate(candidate_str: str, policy: NonRelayFilterPolicy) -> bool:
    """Return True if this ICE candidate should be kept under the given policy.

    Relay candidates always pass — a relay with a private IP (LAN TURN server)
    must never be dropped regardless of policy.
    """
    if " typ relay" in candidate_str:
        return True
    if policy == NonRelayFilterPolicy.NONE:
        return True
    if policy == NonRelayFilterPolicy.ALL:
        return False
    # PRIVATE: drop non-relay candidates with private/CGNAT IPs
    return not is_private_ip_candidate(candidate_str)


def filter_outbound_sdp(sdp: str) -> str:
    """Strip ICE candidates from an outbound answer SDP based on ICE_OUTBOUND_POLICY."""
    if ICE_OUTBOUND_POLICY == NonRelayFilterPolicy.NONE:
        return sdp

    lines = sdp.split("\r\n")
    filtered: List[str] = []
    dropped = 0
    kept_relay = 0
    for line in lines:
        if line.startswith("a=candidate:"):
            candidate_str = line[2:]
            if not _keep_candidate(candidate_str, ICE_OUTBOUND_POLICY):
                dropped += 1
                continue
            if " typ relay" in candidate_str:
                kept_relay += 1
        filtered.append(line)

    if ICE_OUTBOUND_POLICY == NonRelayFilterPolicy.ALL:
        if kept_relay == 0:
            logger.warning(
                "FORCE_TURN_RELAY is on but the answer SDP has no relay candidates "
                f"(dropped {dropped} non-relay). TURN may be unreachable; "
                "the connection will fail."
            )
        else:
            logger.info(
                f"FORCE_TURN_RELAY: kept {kept_relay} relay candidates, "
                f"dropped {dropped} non-relay"
            )

    return "\r\n".join(filtered)


def get_ice_servers(user_id: Optional[str] = None) -> List[RTCIceServer]:
    """Build ICE servers configuration including TURN if configured.

    Args:
        user_id: Optional user ID for generating time-limited TURN credentials.
                 If provided and TURN_SECRET is configured, uses TURN REST API.

    Returns:
        List of RTCIceServer configurations for WebRTC peer connection.
    """
    servers: List[RTCIceServer] = [RTCIceServer(urls="stun:stun.l.google.com:19302")]

    # Check if TURN is configured
    if not TURN_HOST:
        return servers

    # Use time-limited credentials if TURN_SECRET is configured (recommended)
    if TURN_SECRET and user_id:
        try:
            credentials = generate_turn_credentials(user_id)
            servers.append(
                RTCIceServer(
                    urls=credentials["uris"],
                    username=credentials["username"],
                    credential=credentials["password"],
                )
            )
            logger.info(
                f"TURN server configured with time-limited credentials, TTL: {credentials['ttl']}s"
            )
            return servers
        except Exception as e:
            logger.error(f"Failed to generate TURN credentials: {e}")

    # Fallback to static credentials (legacy mode - not recommended for production)
    turn_username = os.getenv("TURN_USERNAME")
    turn_password = os.getenv("TURN_PASSWORD")

    if turn_username and turn_password:
        servers.append(
            RTCIceServer(
                urls=[
                    f"turn:{TURN_HOST}:{TURN_PORT}",
                    f"turn:{TURN_HOST}:{TURN_PORT}?transport=tcp",
                ],
                username=turn_username,
                credential=turn_password,
            )
        )
        logger.warning(
            f"TURN server configured with static credentials (consider using TURN_SECRET for time-limited auth)"
        )

    return servers


class SignalingManager:
    """Manages WebSocket connections and WebRTC peer connections."""

    def __init__(self):
        self._connections: Dict[str, WebSocket] = {}
        self._peer_connections: Dict[str, SmallWebRTCConnection] = {}

    async def handle_websocket(
        self,
        websocket: WebSocket,
        workflow_id: int,
        workflow_run_id: int,
        user: UserModel,
    ):
        """Handle WebSocket connection for signaling."""
        await websocket.accept()
        connection_id = f"{workflow_id}:{workflow_run_id}:{user.id}"
        self._connections[connection_id] = websocket

        try:
            while True:
                message = await websocket.receive_json()
                await self._handle_message(
                    websocket, message, workflow_id, workflow_run_id, user
                )
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected for {connection_id}")
        except Exception as e:
            logger.error(f"WebSocket error for {connection_id}: {e}")
        finally:
            # Cleanup
            self._connections.pop(connection_id, None)

            # Unregister WebSocket sender for real-time feedback
            unregister_ws_sender(workflow_run_id)

            # Clean up all peer connections for this workflow run
            # Note: In a WebSocket-based signaling approach (vs HTTP PATCH),
            # we maintain our own connection map instead of relying on
            # SmallWebRTCRequestHandler's _pcs_map. This is suitable for
            # multi-worker FastAPI deployments where state cannot be shared.
            for pc_id in list(self._peer_connections.keys()):
                pc = self._peer_connections.pop(pc_id, None)
                if pc:
                    await pc.disconnect()
                    logger.debug(f"Disconnected peer connection: {pc_id}")

    async def _handle_message(
        self,
        ws: WebSocket,
        message: dict,
        workflow_id: int,
        workflow_run_id: int,
        user: UserModel,
    ):
        """Handle incoming WebSocket messages."""
        msg_type = message.get("type")
        payload = message.get("payload", {})

        if msg_type == "offer":
            await self._handle_offer(ws, payload, workflow_id, workflow_run_id, user)
        elif msg_type == "ice-candidate":
            await self._handle_ice_candidate(ws, payload, workflow_run_id)
        elif msg_type == "renegotiate":
            await self._handle_renegotiation(ws, payload, workflow_id, workflow_run_id)

    async def _handle_offer(
        self,
        ws: WebSocket,
        payload: dict,
        workflow_id: int,
        workflow_run_id: int,
        user: UserModel,
    ):
        """Handle offer message and create answer with ICE trickling."""
        pc_id = payload.get("pc_id")
        sdp = payload.get("sdp")
        type_ = payload.get("type")
        call_context_vars = payload.get("call_context_vars", {})

        # Set run context for logging and tracing. org_id must be set before
        # pc.initialize() so that aiortc's internal tasks inherit it.
        set_current_run_id(workflow_run_id)
        org_id = await db_client.get_workflow_organization_id(workflow_id)
        if org_id:
            set_current_org_id(org_id)

        # Check Dograh quota before initiating the call (apply per-workflow
        # model_overrides so we evaluate the keys this workflow will use).
        quota_result = await check_dograh_quota(user, workflow_id=workflow_id)
        if not quota_result.has_quota:
            # Send error response for quota issues
            await ws.send_json(
                {
                    "type": "error",
                    "payload": {
                        "error_type": quota_result.error_code,
                        "message": quota_result.error_message,
                    },
                }
            )
            return

        if pc_id and pc_id in self._peer_connections:
            # Reuse existing connection
            logger.info(f"Reusing existing connection for pc_id: {pc_id}")
            pc = self._peer_connections[pc_id]
            await pc.renegotiate(sdp=sdp, type=type_, restart_pc=False)

            # Send updated answer
            answer = pc.get_answer()
            await ws.send_json(
                {
                    "type": "answer",
                    "payload": {
                        "sdp": filter_outbound_sdp(answer["sdp"]),
                        "type": "answer",
                        "pc_id": pc_id,
                    },
                }
            )
        else:
            # Create new connection using correct SmallWebRTC API
            # Generate ICE servers with time-limited TURN credentials for this user
            user_ice_servers = get_ice_servers(user_id=str(user.id))
            pc = SmallWebRTCConnection(
                ice_servers=user_ice_servers, connection_timeout_secs=60
            )
            # Set the pc_id before initialization so it's available in get_answer()
            pc._pc_id = pc_id

            # Initialize connection with offer
            await pc.initialize(sdp=sdp, type=type_)

            # Store peer connection using client's pc_id
            self._peer_connections[pc_id] = pc

            # Register WebSocket sender for real-time feedback
            async def ws_sender(message: dict):
                if ws.application_state == WebSocketState.CONNECTED:
                    await ws.send_json(message)

            register_ws_sender(workflow_run_id, ws_sender)

            # Setup closed handler
            @pc.event_handler("closed")
            async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
                logger.info(f"PeerConnection closed: {webrtc_connection.pc_id}")
                self._peer_connections.pop(webrtc_connection.pc_id, None)

            # Start pipeline in background
            asyncio.create_task(
                run_pipeline_smallwebrtc(
                    pc,
                    workflow_id,
                    workflow_run_id,
                    user.id,
                    call_context_vars,
                    user_provider_id=str(user.provider_id),
                )
            )

            # Get answer after initialization
            answer = pc.get_answer()

            # Send answer immediately (ICE candidates will be sent separately via trickling)
            await ws.send_json(
                {
                    "type": "answer",
                    "payload": {
                        "sdp": filter_outbound_sdp(answer["sdp"]),
                        "type": answer["type"],
                        "pc_id": answer["pc_id"],
                    },
                }
            )

    async def _handle_ice_candidate(
        self, ws: WebSocket, payload: dict, workflow_run_id: int
    ):
        """Handle incoming ICE candidate from client.

        Uses SmallWebRTC's native ICE trickling support via add_ice_candidate().
        Candidates are parsed using aiortc's candidate_from_sdp() for proper formatting,
        consistent with SmallWebRTCRequestHandler.handle_patch_request().
        Candidates are filtered according to ICE_INBOUND_POLICY before being added.
        """
        pc_id = payload.get("pc_id")
        candidate_data = payload.get("candidate")

        if not pc_id:
            logger.warning("Received ICE candidate without pc_id")
            return

        pc = self._peer_connections.get(pc_id)
        if not pc:
            logger.warning(f"No peer connection found for pc_id: {pc_id}")
            return

        if candidate_data:
            candidate_str = candidate_data.get("candidate", "")

            if not _keep_candidate(candidate_str, ICE_INBOUND_POLICY):
                logger.debug(
                    f"Dropping inbound candidate per policy ({ICE_INBOUND_POLICY.value}): {candidate_str[:50]}..."
                )
                return

            try:
                # Parse the ICE candidate using aiortc's parser (same as SmallWebRTCRequestHandler)
                candidate = candidate_from_sdp(candidate_str)
                candidate.sdpMid = candidate_data.get("sdpMid")
                candidate.sdpMLineIndex = candidate_data.get("sdpMLineIndex")

                await pc.add_ice_candidate(candidate)
                logger.debug(f"Added ICE candidate for pc_id: {pc_id}")
            except Exception as e:
                logger.error(f"Failed to add ICE candidate: {e}")
        else:
            logger.debug(f"End of ICE candidates for pc_id: {pc_id}")

    async def _handle_renegotiation(
        self, ws: WebSocket, payload: dict, workflow_id: int, workflow_run_id: int
    ):
        """Handle renegotiation request."""
        pc_id = payload.get("pc_id")
        sdp = payload.get("sdp")
        type_ = payload.get("type")
        restart_pc = payload.get("restart_pc", False)

        if not pc_id or pc_id not in self._peer_connections:
            await ws.send_json(
                {"type": "error", "payload": {"message": "Peer connection not found"}}
            )
            return

        pc = self._peer_connections[pc_id]
        await pc.renegotiate(sdp=sdp, type=type_, restart_pc=restart_pc)

        # Send updated answer
        answer = pc.get_answer()
        await ws.send_json(
            {
                "type": "answer",
                "payload": {
                    "sdp": filter_outbound_sdp(answer["sdp"]),
                    "type": "answer",
                    "pc_id": pc_id,  # Use the client's pc_id
                },
            }
        )


# Create singleton instance
signaling_manager = SignalingManager()


@router.websocket("/signaling/{workflow_id}/{workflow_run_id}")
async def signaling_websocket(
    websocket: WebSocket,
    workflow_id: int,
    workflow_run_id: int,
    user: UserModel = Depends(get_user_ws),
):
    """WebSocket endpoint for WebRTC signaling with ICE trickling."""
    workflow_run = await db_client.get_workflow_run(workflow_run_id, user.id)
    if not workflow_run:
        logger.warning(f"workflow run {workflow_run_id} not found for user {user.id}")
        raise HTTPException(status_code=400, detail="Bad workflow_run_id")

    await signaling_manager.handle_websocket(
        websocket, workflow_id, workflow_run_id, user
    )


@router.websocket("/public/signaling/{session_token}")
async def public_signaling_websocket(
    websocket: WebSocket,
    session_token: str,
):
    """Public WebSocket endpoint for WebRTC signaling with embed tokens.

    This endpoint:
    1. Validates the session token from embed initialization
    2. Retrieves the associated workflow run
    3. Handles WebRTC signaling without requiring authentication
    """

    # Validate session token
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        await websocket.close(code=1008, reason="Invalid session token")
        return

    # Check if session is expired
    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        await websocket.close(code=1008, reason="Session expired")
        return

    # Get the embed token for user information
    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        await websocket.close(code=1008, reason="Invalid embed token")
        return

    # Create a minimal user object for compatibility with signaling manager
    # Use the embed token creator as the user
    user = await db_client.get_user_by_id(embed_token.created_by)
    if not user:
        await websocket.close(code=1008, reason="Invalid user")
        return

    # Handle the WebSocket connection using the existing signaling manager
    await signaling_manager.handle_websocket(
        websocket, embed_token.workflow_id, embed_session.workflow_run_id, user
    )
