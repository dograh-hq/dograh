"""ARQ worker configuration - setup logging before importing tasks"""

import ssl
from urllib.parse import urlparse

from api.constants import REDIS_URL

# Setup logging - this is now idempotent and safe to call multiple times
from api.logging_config import setup_logging
from api.tasks.function_names import FunctionNames

setup_logging()

# Now import ARQ and task dependencies
from arq import create_pool, cron
from arq.connections import ArqRedis, RedisSettings

parsed_url = urlparse(REDIS_URL)

# Check if we're using TLS (rediss://)
use_ssl = parsed_url.scheme == "rediss"

# Create SSL context if using rediss://
ssl_context = None
if use_ssl:
    ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

REDIS_SETTINGS = RedisSettings(
    host=parsed_url.hostname or "localhost",
    port=parsed_url.port or 6379,
    password=parsed_url.password,
    conn_timeout=10,
    ssl=use_ssl,
    ssl_ca_certs=None if not use_ssl else None,
    ssl_certfile=None,
    ssl_keyfile=None,
    ssl_check_hostname=False if use_ssl else None,
)

from api.tasks.campaign_tasks import (
    process_campaign_batch,
    sync_campaign_source,
)
from api.tasks.knowledge_base_processing import process_knowledge_base_document
from api.tasks.run_integrations import run_integrations_post_workflow_run
from api.tasks.webhook_delivery import deliver_webhook, sweep_webhook_deliveries
from api.tasks.workflow_completion import process_workflow_completion


class WorkerSettings:
    functions = [
        run_integrations_post_workflow_run,
        process_workflow_completion,
        sync_campaign_source,
        process_campaign_batch,
        process_knowledge_base_document,
        deliver_webhook,
    ]
    cron_jobs = [
        # Safety net for webhook deliveries whose ARQ job was lost (worker
        # restart / Redis flush): re-enqueue any pending delivery that is overdue.
        cron(
            sweep_webhook_deliveries,
            minute=set(range(0, 60, 5)),
            second=0,
            run_at_startup=True,
        ),
    ]
    redis_settings = REDIS_SETTINGS
    max_jobs = 10


LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    # --- Handlers ---
    "handlers": {
        "console": {  # everything goes to stdout
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "level": "WARNING",  # only WARNING and above
            "formatter": "simple",
        },
    },
    # --- Formatters (optional) ---
    "formatters": {
        "simple": {
            "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        },
    },
    # --- Root logger ---
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    # --- Optionally silence Arq itself explicitly ---
    "loggers": {
        "arq": {  # arq.* loggers
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}


_redis_pool: ArqRedis | None = None


async def get_arq_redis() -> ArqRedis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(REDIS_SETTINGS)
    return _redis_pool


async def enqueue_job(function_name: FunctionNames, *args, **kwargs):
    redis = await get_arq_redis()
    # kwargs forwards ARQ job options (e.g. _job_id, _defer_by) used for
    # deterministic, backed-off webhook delivery retries.
    return await redis.enqueue_job(function_name, *args, **kwargs)
