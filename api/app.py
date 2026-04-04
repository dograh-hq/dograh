"""Set up logging before importing anything else"""

import sentry_sdk

from api.constants import DEPLOYMENT_MODE, ENABLE_TELEMETRY, SENTRY_DSN
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


from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.constants import REDIS_URL
from api.routes.main import router as main_router
from api.services.pipecat.tracing_config import (
    handle_langfuse_sync,
    load_all_org_langfuse_credentials,
)
from api.services.worker_sync.manager import (
    WorkerSyncManager,
    set_worker_sync_manager,
)
from api.services.worker_sync.protocol import WorkerSyncEventType
from api.tasks.arq import get_arq_redis

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # warmup arq pool
    await get_arq_redis()

    # Pre-register all org-specific Langfuse exporters so they're ready
    # before any pipeline runs, without per-call DB lookups.
    await load_all_org_langfuse_credentials()

    # Start cross-worker sync manager so config changes propagate to all workers
    sync_manager = WorkerSyncManager(REDIS_URL)
    sync_manager.register(
        WorkerSyncEventType.LANGFUSE_CREDENTIALS, handle_langfuse_sync
    )
    await sync_manager.start()
    set_worker_sync_manager(sync_manager)

    yield  # Run app

    # Shutdown sequence - this runs when FastAPI is shutting down
    logger.info("Starting graceful shutdown...")
    await sync_manager.stop()


app = FastAPI(
    title="Dograh API",
    description="API for the Dograh app",
    version="1.0.0",
    openapi_url=f"{API_PREFIX}/openapi.json",
    lifespan=lifespan,
    servers=[
        {"url": "https://app.dograh.com", "description": "Production"},
        {"url": "http://localhost:8000", "description": "Local development"},
    ],
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
