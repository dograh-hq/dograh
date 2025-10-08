"""Set up logging before importing anything else"""

import sentry_sdk

from api.constants import DEPLOYMENT_MODE, ENABLE_TELEMETRY, REDIS_URL, SENTRY_DSN
from api.logging_config import ENVIRONMENT, setup_logging

# Set up logging and get the listener for cleanup
setup_logging()


if SENTRY_DSN and (
    DEPLOYMENT_MODE != "oss" or (DEPLOYMENT_MODE == "oss" and ENABLE_TELEMETRY)
):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=True,
        environment=ENVIRONMENT,
    )
    print(f"Sentry initialized in environment: {ENVIRONMENT}")


import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.routes.main import router as main_router
from api.routes.rtc_offer import pcs_map
from api.services.telephony.worker_event_subscriber import (
    WorkerEventSubscriber,
    setup_worker_subscriber,
)
from api.tasks.arq import get_arq_redis

API_PREFIX = "/api/v1"

# Global reference to worker subscriber for graceful shutdown
worker_subscriber_instance: Optional[WorkerEventSubscriber] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker_subscriber_instance

    # warmup arq pool
    await get_arq_redis()

    # Setup Redis connection for distributed mode
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)

    # Setup worker subscriber (ARI Manager runs separately)
    worker_subscriber = await setup_worker_subscriber(redis)
    worker_subscriber_instance = worker_subscriber

    # Store worker ID in app state for health check
    app.state.worker_id = worker_subscriber.worker_id
    app.state.worker_subscriber = worker_subscriber

    yield  # Run app

    # Shutdown sequence - this runs when FastAPI is shutting down
    logger.info("Starting graceful shutdown...")

    # First, try graceful shutdown with timeout
    if worker_subscriber:
        try:
            # Check if we should do graceful shutdown (e.g., if SIGTERM was received)
            # For now, we'll attempt graceful shutdown for all shutdowns
            await worker_subscriber.graceful_shutdown(max_wait_seconds=300)
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
            # Fall back to immediate stop
            await worker_subscriber.stop()

    # close all dangling pipecat connections
    coros = [pc.close() for pc in pcs_map.values()]
    await asyncio.gather(*coros)
    pcs_map.clear()

    await redis.aclose()


app = FastAPI(
    title="Dograh API",
    description="API for the Dograh app",
    version="1.0.0",
    openapi_url=f"{API_PREFIX}/openapi.json",
    lifespan=lifespan,
)


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

api_router = APIRouter()

# include subrouters here
api_router.include_router(main_router)

# main router with api prefix
app.include_router(api_router, prefix=API_PREFIX)
