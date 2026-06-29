"""Set up logging before importing anything else"""

import sentry_sdk

from api.constants import (
    CORS_ALLOWED_ORIGINS,
    DEPLOYMENT_MODE,
    ENABLE_TELEMETRY,
    SENTRY_DSN,
)
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
from api.mcp_server import mcp
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

mcp_app = mcp.http_app(path="/", stateless_http=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_app.lifespan(app):
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
    title="Auto4You Voice API",
    description=(
        "REST API for the Auto4You voice-agent platform.\n\n"
        "**Authentication:** pass your organization API key in the `X-API-Key` "
        "header (create one in the dashboard under API Keys). Public call-trigger "
        "endpoints are grouped under the **public** tag.\n\n"
        "Outbound calling requires completed KYC and available call minutes."
    ),
    version="1.0.0",
    openapi_url=f"{API_PREFIX}/openapi.json",
    lifespan=lifespan,
    servers=[
        {"url": "https://api.auto4you.in", "description": "Production"},
        {"url": "http://localhost:8000", "description": "Local development"},
    ],
)


# Configure CORS. The API authenticates purely via the `Authorization: Bearer`
# header (set explicitly by clients) and uses NO cookies, so credentialed CORS is
# unnecessary. The previous `allow_origins=["*"]` WITH `allow_credentials=True` was
# the actual vulnerability — it let ANY website make credentialed cross-origin
# requests. Setting allow_credentials=False closes that hole while keeping the public
# API and the cross-origin embed widget working (a strict origin allow-list would
# break the widget, whose preflight the middleware intercepts before per-token domain
# validation in routes/public_embed.py can run).
#
# NOTE: Upstream dograh-v1.38.0 switched non-oss deployments to a strict
# CORS_ALLOWED_ORIGINS allowlist with allow_credentials=True. We intentionally do
# NOT adopt that branch here: this SaaS fork relies on the cross-origin embed widget
# (see routes/public_embed.py), which a strict allowlist would break, and our Bearer/
# no-cookie auth model makes credentialed CORS unnecessary. The permissive wildcard +
# allow_credentials=False below is safe and is kept for both OSS and SaaS modes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _add_public_embed_cors_middleware() -> None:
    from api.routes.public_embed import PublicEmbedCORSMiddleware

    app.add_middleware(PublicEmbedCORSMiddleware, api_prefix=API_PREFIX)


_add_public_embed_cors_middleware()

api_router = APIRouter()

# include subrouters here
api_router.include_router(main_router)

# main router with api prefix
app.include_router(api_router, prefix=API_PREFIX)

# Mount the MCP server — agents reach it at /api/v1/mcp over Streamable HTTP,
# authenticating with the same X-API-Key header used by the REST API.
# Mounted under /api/v1 so existing reverse-proxy rules (nginx etc.) route it
# without any extra configuration.
app.mount(f"{API_PREFIX}/mcp", mcp_app)
