"""Session lifecycle — create/update session records via Dograh API."""

import logging
from app.dograh_client import DograhClient
from app.config import settings

logger = logging.getLogger(__name__)


async def fetch_agent_config(raw_metadata: str) -> dict:
    """Fetch agent config from Dograh API based on room metadata."""
    import json
    client = DograhClient(settings)

    meta = json.loads(raw_metadata or "{}")
    deploy_id = meta.get("deploy_id", "")
    if not deploy_id:
        raise ValueError("deploy_id missing from room metadata")

    config = await client.fetch_runtime_config(deploy_id)
    config_dict = config.model_dump()
    config_dict["channel"] = meta.get("channel", "voice_sip")
    config_dict["sender_phone"] = meta.get("sender_phone", "")
    config_dict["campaign_id"] = meta.get("campaign_id", "")
    config_dict["lead_id"] = meta.get("lead_id", "")

    return config_dict


async def write_session_record(
    deploy_id: str, org_id: str, room_name: str, channel: str, agent_id: str, **kwargs
) -> dict:
    client = DograhClient(settings)
    return await client.create_session(
        deploy_id=deploy_id, org_id=org_id, room_name=room_name,
        channel=channel, agent_id=agent_id, **kwargs,
    )


async def hangup_cleanup(
    session_id: str, org_id: str, deploy_id: str,
    room_name: str, duration_sec: float, channel: str,
) -> None:
    from livekit.api import LiveKitAPI
    from livekit.protocol.room import DeleteRoomRequest

    try:
        async with LiveKitAPI(
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        ) as lk_api:
            await lk_api.room.delete_room(DeleteRoomRequest(room=room_name))
            logger.info(f"LiveKit room deleted: {room_name}")
    except Exception as e:
        logger.warning(f"Failed to delete room {room_name}: {e}")

    client = DograhClient(settings)
    try:
        await client.hangup_session(
            session_id=session_id, org_id=org_id, deploy_id=deploy_id,
            room_name=room_name, duration_sec=duration_sec,
            outcome="completed", channel=channel,
        )
    except Exception as e:
        logger.warning(f"Failed to send hangup webhook: {e}")
