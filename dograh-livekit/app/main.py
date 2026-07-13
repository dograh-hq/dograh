"""LiveKit AgentServer entrypoint for dograh-livekit."""

import os
import asyncio
import logging
from livekit import agents
from livekit.agents import AgentServer

from app.config import settings

os.environ["GOOGLE_API_KEY"] = settings.google_api_key
os.environ["OPENAI_API_KEY"] = settings.openai_api_key
os.environ["LIVEKIT_URL"] = settings.livekit_url
os.environ["LIVEKIT_API_KEY"] = settings.livekit_api_key
os.environ["LIVEKIT_API_SECRET"] = settings.livekit_api_secret

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

server = AgentServer(
    ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key,
    api_secret=settings.livekit_api_secret,
    port=0,
    num_idle_processes=1,
    load_threshold=1.0,
    job_executor_type=agents.JobExecutorType.THREAD,
)


@server.rtc_session(agent_name="dograh-agent")
async def dograh_session(ctx: agents.JobContext):
    from app.entrypoint import lumina_session
    await lumina_session(ctx)


async def serve():
    await server.run()


if __name__ == "__main__":
    asyncio.run(serve())
