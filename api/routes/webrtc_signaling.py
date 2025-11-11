"""WebSocket-based WebRTC signaling endpoint with ICE trickling support.

This implementation uses WebSocket-based signaling instead of HTTP PATCH for ICE candidates,
which is suitable for multi-worker FastAPI deployments where local _pcs_map cannot be shared.

Uses the SmallWebRTC API contract:
- SmallWebRTCConnection for peer connection management
- candidate_from_sdp() for parsing ICE candidates
- add_ice_candidate() for trickling support
"""

import asyncio
from datetime import UTC, datetime
from typing import Dict

from aiortc.sdp import candidate_from_sdp
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user_ws
from api.services.pipecat.run_pipeline import run_pipeline_smallwebrtc
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.utils.context import set_current_run_id

router = APIRouter(prefix="/ws")

# ICE servers configuration
ice_servers = ["stun:stun.l.google.com:19302"]


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

        # Set run context for logging
        set_current_run_id(workflow_run_id)

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
                    "payload": {"sdp": answer["sdp"], "type": "answer", "pc_id": pc_id},
                }
            )
        else:
            # Create new connection using correct SmallWebRTC API
            pc = SmallWebRTCConnection(
                ice_servers=ice_servers, connection_timeout_secs=60
            )
            # Set the pc_id before initialization so it's available in get_answer()
            pc._pc_id = pc_id

            # Initialize connection with offer
            await pc.initialize(sdp=sdp, type=type_)

            # Store peer connection using client's pc_id
            self._peer_connections[pc_id] = pc

            # Setup closed handler
            @pc.event_handler("closed")
            async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
                logger.info(f"PeerConnection closed: {webrtc_connection.pc_id}")
                self._peer_connections.pop(webrtc_connection.pc_id, None)

            # Start pipeline in background
            asyncio.create_task(
                run_pipeline_smallwebrtc(
                    pc, workflow_id, workflow_run_id, user.id, call_context_vars
                )
            )

            # Get answer after initialization
            answer = pc.get_answer()

            # Send answer immediately (ICE candidates will be sent separately via trickling)
            await ws.send_json(
                {
                    "type": "answer",
                    "payload": {
                        "sdp": answer["sdp"],
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
            try:
                # Parse the ICE candidate using aiortc's parser (same as SmallWebRTCRequestHandler)
                candidate = candidate_from_sdp(candidate_data["candidate"])
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
                    "sdp": answer["sdp"],
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
