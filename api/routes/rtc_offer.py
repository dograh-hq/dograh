from typing import Dict

from fastapi import APIRouter, BackgroundTasks, Depends
from loguru import logger
from pydantic import BaseModel

from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.pipecat.run_pipeline import run_pipeline_smallwebrtc
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.utils.context import set_current_run_id

router = APIRouter(prefix="/pipecat")

pcs_map: Dict[str, SmallWebRTCConnection] = {}
ice_servers = ["stun:stun.l.google.com:19302"]


class RTCOfferRequest(BaseModel):
    pc_id: str | None
    sdp: str
    type: str
    workflow_id: int
    workflow_run_id: int
    restart_pc: bool = False
    call_context_vars: dict | None = None


@router.post("/rtc-offer")
async def offer(
    request: RTCOfferRequest,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_user),
):
    pc_id = request.pc_id

    if pc_id and pc_id in pcs_map:
        # Ensure run_id context is available for logs even when reusing an existing PC.
        set_current_run_id(request.workflow_run_id)

        pipecat_connection = pcs_map[pc_id]
        logger.info(f"Reusing existing connection for pc_id: {pc_id}")
        await pipecat_connection.renegotiate(
            sdp=request.sdp,
            type=request.type,
            restart_pc=request.restart_pc,
        )
    else:
        # Set the run_id *before* creating the SmallWebRTCConnection so that all
        # async tasks and event-handler coroutines spawned inside the
        # constructor inherit the correct context variable value.  Otherwise the
        # default ("NA") leaks into the log output produced by those tasks.
        set_current_run_id(request.workflow_run_id)

        pipecat_connection = SmallWebRTCConnection(ice_servers)
        await pipecat_connection.initialize(sdp=request.sdp, type=request.type)

        @pipecat_connection.event_handler("closed")
        async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
            logger.info(
                f"In pipecat connection closed handler. Popping peer connection pc_id: {webrtc_connection.pc_id} from pcs_map"
            )
            pcs_map.pop(webrtc_connection.pc_id, None)

        background_tasks.add_task(
            run_pipeline_smallwebrtc,
            pipecat_connection,
            request.workflow_id,
            request.workflow_run_id,
            user.id,
            request.call_context_vars or {},
        )

    answer = pipecat_connection.get_answer()
    pcs_map[answer["pc_id"]] = pipecat_connection

    return answer
