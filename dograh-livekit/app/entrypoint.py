"""RTC session entrypoint — main handler for LiveKit AgentServer."""

import asyncio
import json
import logging
import time
from livekit import agents

from app.config import settings
from app.session.lifecycle import fetch_agent_config, write_session_record, hangup_cleanup
from app.session.flow import build_runtime_variables

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def _wait_for_sip_disconnect(ctx: agents.JobContext, timeout: float = 60.0) -> None:
    participant_joined = asyncio.Event()
    participant_left = asyncio.Event()

    def _on_connected(participant) -> None:
        identity = getattr(participant, "identity", "")
        if identity.startswith("sip_"):
            logger.info("SIP participant connected: %s", identity)
            participant_joined.set()

    def _on_disconnected(participant) -> None:
        identity = getattr(participant, "identity", "")
        if identity.startswith("sip_"):
            logger.info("SIP participant disconnected: %s", identity)
            participant_left.set()

    ctx.room.on("participant_connected", _on_connected)
    ctx.room.on("participant_disconnected", _on_disconnected)

    try:
        for p in ctx.room.remote_participants.values():
            _on_connected(p)
        await asyncio.wait_for(participant_joined.wait(), timeout=timeout)
        await participant_left.wait()
    except asyncio.TimeoutError:
        logger.info("No SIP participant joined within timeout; closing room")
    finally:
        ctx.room.off("participant_connected", _on_connected)
        ctx.room.off("participant_disconnected", _on_disconnected)


async def lumina_session(ctx: agents.JobContext):
    """Main entry point for all Dograh-LiveKit agent sessions."""
    session = None
    t_start = time.monotonic()
    session_id = ""
    org_id = ""
    deploy_id = ""
    channel = ""

    try:
        await ctx.connect()

        raw_metadata = ctx.job.room.metadata or ""
        meta = json.loads(raw_metadata or "{}")
        deploy_id = meta.get("deploy_id", "") or str(meta.get("workflow_id", ""))
        workflow_id = int(meta.get("workflow_id", deploy_id)) if meta.get("workflow_id") or deploy_id.isdigit() else 0
        channel = meta.get("channel", "voice_sip")
        org_id = meta.get("org_id", "")

        if not deploy_id and not workflow_id:
            raise ValueError("workflow_id missing from room metadata")
        if not org_id:
            raise ValueError("org_id missing from room metadata")

        logger.info(f"Session start — room={ctx.room.name} workflow={workflow_id or deploy_id} channel={channel}")

        config = await fetch_agent_config(raw_metadata)
        config["workflow_id"] = workflow_id or int(deploy_id)
        config["org_id"] = org_id
        config["sender_phone"] = meta.get("sender_phone", "")
        config["channel"] = channel
        config["user_id"] = meta.get("user_id") or meta.get("sender_phone") or ""

        llm_cfg = config.get("llm_config") or {}
        llm_model = str(llm_cfg.get("model") or "unknown")
        session_record = await write_session_record(
            workflow_id=workflow_id or int(deploy_id), org_id=org_id, room_name=ctx.room.name,
            channel=channel, agent_id=str(config.get("agent_id") or ""),
            llm_model=llm_model,
        )
        session_id = str(session_record.get("id", ""))
        config["session_id"] = session_id

        flow_vars = build_runtime_variables(config)
        config["flow_variables"] = flow_vars

        from app.session.voice import voice_session
        logger.info("Starting voice session...")
        session = await voice_session(ctx, config)
        logger.info(f"Session started in {time.monotonic() - t_start:.2f}s")

        if channel == "voice_sip":
            await _wait_for_sip_disconnect(ctx)
        else:
            await session.wait_for_inactive()

        duration = time.monotonic() - t_start
        logger.info(f"Session completed — duration={duration:.1f}s")

    except Exception as exc:
        logger.error(f"Agent exception in room {ctx.room.name}: {exc}", exc_info=True)
        if session is not None:
            try:
                await session.say("Mi dispiace, si è verificato un problema. Richiami più tardi.")
            except Exception:
                pass
        duration = time.monotonic() - t_start
    else:
        duration = time.monotonic() - t_start

    finally:
        if session_id and org_id:
            await hangup_cleanup(
                session_id=session_id, org_id=org_id, workflow_id=workflow_id or int(deploy_id),
                room_name=ctx.room.name, duration_sec=duration, channel=channel,
            )
