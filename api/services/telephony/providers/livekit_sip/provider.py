"""LiveKit SIP telephony provider for outbound campaign calls.

This provider uses LiveKit Cloud SIP trunks for outbound calls,
dispatching them directly to the dograh-livekit AgentServer instead
of going through Pipecat pipelines.
"""

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from api.services.telephony.base import CallInitiationResult, TelephonyProvider

if TYPE_CHECKING:
    pass  # No fastapi imports needed at class level


class LiveKitSipProvider(TelephonyProvider):
    """Outbound calls via LiveKit Cloud SIP trunks.

    This provider does NOT use Pipecat. Instead, it creates a LiveKit room
    with the call metadata and lets the dograh-livekit AgentServer pick up
    the job. The SIP participant is dialed by LiveKit Cloud.
    """

    PROVIDER_NAME = "livekit_sip"
    WEBHOOK_ENDPOINT = ""  # Not used — LiveKit dispatches directly to AgentServer

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._sip_trunk_id = config.get("sip_trunk_id", "")
        self.from_numbers = config.get("from_numbers", [])

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: int,
        from_number: str | None = None,
        **kwargs,
    ) -> CallInitiationResult:
        """Initiate outbound SIP call via LiveKit Cloud."""
        import os
        from livekit import api as lk_api

        lk_url = os.getenv("LIVEKIT_URL", "")
        lk_api_key = os.getenv("LIVEKIT_API_KEY", "")
        lk_api_secret = os.getenv("LIVEKIT_API_SECRET", "")

        if not all([lk_url, lk_api_key, lk_api_secret]):
            raise RuntimeError("LiveKit credentials not configured")

        lkapi = lk_api.LiveKitAPI(
            url=lk_url,
            api_key=lk_api_key,
            api_secret=lk_api_secret,
        )
        try:
            room_name = f"dograh-call-{workflow_run_id}"

            metadata = json.dumps({
                "deploy_id": str(kwargs.get("workflow_id", "")),
                "org_id": str(kwargs.get("organization_id", "")),
                "channel": "voice_sip",
                "sender_phone": to_number,
                "campaign_id": str(kwargs.get("campaign_id", "")),
                "lead_id": str(kwargs.get("lead_id", "")),
            })

            participant = await lkapi.sip.create_sip_participant(
                lk_api.CreateSIPParticipantRequest(
                    room_name=room_name,
                    sip_trunk_id=self._sip_trunk_id,
                    participant_identity=f"sip_out_{workflow_run_id}",
                    participant_name=to_number,
                    sip_number=to_number,
                    room_metadata=metadata,
                    agent_dispatch=lk_api.RoomAgentDispatch(
                        agent_name="dograh-agent",
                    ),
                )
            )

            call_id = participant.participant_id or f"lk_{workflow_run_id}"
            logger.info(
                "LiveKit SIP outbound call: room={} call_id={} to={}",
                room_name, call_id, to_number,
            )

            return CallInitiationResult(
                call_id=call_id,
                status="initiated",
                caller_number=from_number,
                provider_metadata={
                    "room_name": room_name,
                    "call_id": call_id,
                },
            )

        finally:
            await lkapi.aclose()
