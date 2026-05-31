"""
paygent_agent_sync.py — Paygent agent auto-registration for Dograh workflows.

Design contract (never break these):
  - Pure REST (requests only). Zero external SDK dependencies.
  - Completely asynchronous at the call site:
      * ensure_agent_async() is a proper coroutine that submits the HTTP call to
        a ThreadPoolExecutor — never blocks the FastAPI event loop.
  - Idempotent: POST /api/v2/agents/ensure returns the existing record unchanged
    if already registered — safe to call on every workflow create and at startup.
  - Nil-safe: all public functions are safe to call when PAYGENT_API_KEY is unset
    or empty — they become silent no-ops.
  - Zero-impact on call pipeline: errors are logged at WARNING level and swallowed.
  - Docker-aware: localhost/127.0.0.1 in PAYGENT_BASE_URL is automatically
    rewritten to host.docker.internal when running inside a container.

Environment variables consumed (all optional, all default-safe):
  PAYGENT_API_KEY                — API key (pk_…). If unset → all calls are no-ops.
  PAYGENT_BASE_URL               — CP tracking service URL. Default: http://localhost:8082
  PAYGENT_AGENT_PRICING_ENABLED  — "true"/"false". Default: false
  PAYGENT_AGENT_PRICE_PER_MINUTE — float. Default: 0.0
  PAYGENT_AGENT_INDICATOR_ID     — indicator name. Default: "per-minute"
  PAYGENT_BACKFILL               — "true" to run backfill at startup. Default: false

Backfill is triggered at startup by run_backfill_if_requested():
  - Reads PAYGENT_BACKFILL=true
  - Uses the same SQLAlchemy async session factory already configured in the app
  - Pages through all active workflows (100 per page)
  - Calls ensure_agent_v2 via thread pool (to not block the event loop during HTTP)
  - Logs a summary banner on completion
  - Non-blocking: runs as asyncio.create_task() so the server starts immediately.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger("api.services.pipecat.paygent_agent_sync")

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "http://localhost:8082"
_REQUEST_TIMEOUT_SECONDS = 15
_BACKFILL_PAGE_SIZE = 100
_BACKFILL_ENV_VAR = "PAYGENT_BACKFILL"
_API_KEY_HEADER = "paygent-api-key"
_ENSURE_AGENT_PATH = "/api/v2/agents/ensure"
_AGENT_TYPE = "voice"

# Shared fire-and-forget thread pool (daemon threads — die with the process).
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="paygent_agent_sync")


# ── Configuration resolution ──────────────────────────────────────────────────

def _resolve_base_url(raw: str) -> str:
    """Rewrite localhost/127.0.0.1 → host.docker.internal when inside Docker."""
    if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER", "").lower() == "true":
        raw = raw.replace("127.0.0.1", "host.docker.internal")
        raw = raw.replace("localhost", "host.docker.internal")
    return raw.rstrip("/")


def _get_config() -> Tuple[Optional[str], str, bool, float, str]:
    """
    Read all Paygent agent-sync config from environment variables.

    Returns:
        (api_key, base_url, pricing_enabled, price_per_minute, indicator_id)
        api_key is None when billing is disabled.
    """
    api_key = os.environ.get("PAYGENT_API_KEY", "").strip() or None
    raw_url = os.environ.get("PAYGENT_BASE_URL", _DEFAULT_BASE_URL).strip()
    base_url = _resolve_base_url(raw_url)
    pricing_enabled = (
        os.environ.get("PAYGENT_AGENT_PRICING_ENABLED", "false").strip().lower() == "true"
    )
    try:
        price_per_minute = float(os.environ.get("PAYGENT_AGENT_PRICE_PER_MINUTE", "0.0").strip())
    except (ValueError, TypeError):
        price_per_minute = 0.0
    indicator_id = (
        os.environ.get("PAYGENT_AGENT_INDICATOR_ID", "per-minute").strip() or "per-minute"
    )
    return api_key, base_url, pricing_enabled, price_per_minute, indicator_id


# ── Core HTTP call ─────────────────────────────────────────────────────────────

def _build_ensure_agent_payload(
    workflow_id: int,
    workflow_name: str,
    pricing_enabled: bool,
    price_per_minute: float,
    indicator_id: str,
) -> Dict[str, Any]:
    """
    Build the POST /api/v2/agents/ensure request body.

    Dograh Workflow → Paygent agent mapping:
      agent_external_id = str(workflow.id)   — stable, DB primary key
      agent_name        = workflow.name
      agent_type        = "voice"
      pricing           = from env vars (optional, operator-configurable)
    """
    payload: Dict[str, Any] = {
        "agent_external_id": str(workflow_id),
        "agent_name":        workflow_name or f"Workflow {workflow_id}",
        "agent_type":        _AGENT_TYPE,
    }

    if pricing_enabled:
        payload["pricing"] = {
            "activityBased": {
                "enabled": True,
                "indicators": {
                    indicator_id: {
                        "enabled":          True,
                        "billingType":      "FLAT",
                        "price":            price_per_minute,
                        "billingFrequency": "Monthly",
                    }
                },
            }
        }

    return payload


def _post_ensure_agent_sync(
    api_key: str,
    base_url: str,
    workflow_id: int,
    workflow_name: str,
    pricing_enabled: bool,
    price_per_minute: float,
    indicator_id: str,
) -> str:
    """
    Execute POST /api/v2/agents/ensure synchronously.

    Always called from a background thread — never directly from the event loop.

    Returns:
        The Paygent agent UUID from the response.

    Raises:
        RuntimeError: On non-200/201 HTTP response.
        requests.exceptions.*: On network errors (let caller classify and log).
    """
    url = f"{base_url}{_ENSURE_AGENT_PATH}"
    headers = {
        "Content-Type": "application/json",
        _API_KEY_HEADER: api_key,
    }
    payload = _build_ensure_agent_payload(
        workflow_id, workflow_name, pricing_enabled, price_per_minute, indicator_id
    )

    resp = requests.post(
        url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS
    )

    # 200 = already existed (idempotent), 201 = newly created
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"ensure-agent returned HTTP {resp.status_code}: {resp.text[:300]}"
        )

    body = resp.json()
    return body.get("id", "")


# ── Async public API ───────────────────────────────────────────────────────────

async def ensure_agent_async(workflow_id: int, workflow_name: str) -> None:
    """
    Idempotently register a dograh workflow as a Paygent agent.

    Completely non-blocking — submits the HTTP call to a thread pool and returns
    immediately. Safe to call on every workflow create path; the Paygent
    /api/v2/agents/ensure endpoint is idempotent (returns existing agent unchanged).

    No-op when PAYGENT_API_KEY is not set.

    Args:
        workflow_id:   Workflow primary key (used as agent_external_id).
        workflow_name: Human-readable workflow name stored in Paygent dashboard.
    """
    try:
        api_key, base_url, pricing_enabled, price_per_minute, indicator_id = _get_config()
        if not api_key:
            return  # Paygent not configured — silent no-op

        loop = asyncio.get_event_loop()

        def _task() -> None:
            try:
                uuid = _post_ensure_agent_sync(
                    api_key, base_url, workflow_id, workflow_name,
                    pricing_enabled, price_per_minute, indicator_id,
                )
                logger.info(
                    "[Paygent AgentSync] ✓ registered workflow_id=%d name=%r → agent_uuid=%s "
                    "pricing_enabled=%s price_per_min=%.4f",
                    workflow_id, workflow_name, uuid, pricing_enabled, price_per_minute,
                )
            except requests.exceptions.Timeout:
                logger.warning(
                    "[Paygent AgentSync] Timeout registering workflow_id=%d — server slow",
                    workflow_id,
                )
            except requests.exceptions.ConnectionError:
                logger.warning(
                    "[Paygent AgentSync] Connection error registering workflow_id=%d "
                    "— server unreachable",
                    workflow_id,
                )
            except Exception as exc:
                logger.warning(
                    "[Paygent AgentSync] Failed to register workflow_id=%d name=%r: %s",
                    workflow_id, workflow_name, exc,
                )

        try:
            loop.run_in_executor(_executor, _task)
        except RuntimeError:
            # Loop or executor already shut down during teardown
            pass

    except Exception as exc:
        # Guarantee this never propagates to the caller
        logger.warning("[Paygent AgentSync] Unexpected error in ensure_agent_async: %s", exc)


# ── Startup backfill ──────────────────────────────────────────────────────────

async def run_backfill_if_requested() -> None:
    """
    Entry point called from app.py lifespan as asyncio.create_task().

    Checks PAYGENT_BACKFILL=true; if set, iterates all active workflows using
    the app's existing async SQLAlchemy session factory and registers each as
    a Paygent agent. HTTP calls run in a thread pool; the event loop remains free.

    The server starts accepting requests immediately — backfill is fully
    non-blocking from the perspective of the startup sequence.
    """
    should_backfill = (
        os.environ.get(_BACKFILL_ENV_VAR, "").strip().lower() == "true"
    )
    if not should_backfill:
        return

    api_key, base_url, pricing_enabled, price_per_minute, indicator_id = _get_config()
    if not api_key:
        logger.warning(
            "[Paygent Backfill] PAYGENT_BACKFILL=true but PAYGENT_API_KEY is not set — skipping."
        )
        return

    logger.info("=== PAYGENT BACKFILL MODE ACTIVATED ===")
    logger.info(
        "[Paygent Backfill] Config: base_url=%s pricing_enabled=%s "
        "price_per_min=%.4f indicator=%s page_size=%d",
        base_url, pricing_enabled, price_per_minute, indicator_id, _BACKFILL_PAGE_SIZE,
    )

    # Import here to avoid circular imports at module load time
    try:
        from sqlalchemy import func
        from sqlalchemy.future import select
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from api.constants import DATABASE_URL
        from api.db.models import WorkflowModel
    except Exception as exc:
        logger.error("[Paygent Backfill] Import error — cannot run backfill: %s", exc)
        return

    loop = asyncio.get_event_loop()
    total = 0
    synced = 0
    failed = 0
    page = 1
    consecutive_db_errors = 0

    # Create a dedicated engine for the backfill (avoids sharing state with app pool)
    try:
        _engine = create_async_engine(DATABASE_URL, pool_size=2, max_overflow=0)
        _session_factory = async_sessionmaker(bind=_engine, expire_on_commit=False)
    except Exception as exc:
        logger.error("[Paygent Backfill] Failed to create DB engine: %s", exc)
        return

    try:
        async with _session_factory() as session:
            # Count active workflows for progress logging
            try:
                count_result = await session.execute(
                    select(func.count(WorkflowModel.id)).where(
                        WorkflowModel.status == "active"
                    )
                )
                total = count_result.scalar() or 0
            except Exception as exc:
                logger.error("[Paygent Backfill] Cannot count active workflows: %s", exc)
                return

            logger.info("[Paygent Backfill] Found %d active workflows to process.", total)

            while True:
                offset = (page - 1) * _BACKFILL_PAGE_SIZE
                try:
                    result = await session.execute(
                        select(WorkflowModel)
                        .where(WorkflowModel.status == "active")
                        .order_by(WorkflowModel.id)
                        .offset(offset)
                        .limit(_BACKFILL_PAGE_SIZE)
                    )
                    workflows = result.scalars().all()
                    consecutive_db_errors = 0
                except Exception as exc:
                    logger.error(
                        "[Paygent Backfill] DB error fetching page %d: %s", page, exc
                    )
                    consecutive_db_errors += 1
                    if consecutive_db_errors > 3:
                        logger.error(
                            "[Paygent Backfill] Too many consecutive DB errors — aborting."
                        )
                        break
                    page += 1
                    continue

                if not workflows:
                    break  # No more rows

                logger.info(
                    "[Paygent Backfill] Processing page %d (%d workflows).",
                    page, len(workflows),
                )

                # Process each workflow: HTTP call runs in thread pool to free the event loop
                for wf in workflows:
                    wf_id = wf.id
                    wf_name = wf.name
                    try:
                        uuid = await loop.run_in_executor(
                            _executor,
                            _post_ensure_agent_sync,
                            api_key, base_url, wf_id, wf_name,
                            pricing_enabled, price_per_minute, indicator_id,
                        )
                        logger.info(
                            "[Paygent Backfill] ✓ synced  id=%-8d name=%r → %s",
                            wf_id, wf_name, uuid,
                        )
                        synced += 1
                    except Exception as exc:
                        logger.warning(
                            "[Paygent Backfill] ✗ FAILED  id=%-8d name=%r error=%s",
                            wf_id, wf_name, exc,
                        )
                        failed += 1

                page += 1

    except Exception as exc:
        logger.error("[Paygent Backfill] Unhandled error during backfill: %s", exc)
    finally:
        try:
            await _engine.dispose()
        except Exception:
            pass


    # Summary banner (always printed)
    logger.info("[Paygent Backfill] ──────────────────────────────────────────────")
    logger.info("[Paygent Backfill] Total:  %d", total)
    logger.info("[Paygent Backfill] Synced: %d", synced)
    logger.info("[Paygent Backfill] Failed: %d", failed)
    logger.info("[Paygent Backfill] ──────────────────────────────────────────────")
    if failed > 0:
        logger.warning(
            "=== PAYGENT BACKFILL COMPLETED WITH %d FAILURES — server running normally ===",
            failed,
        )
    else:
        logger.info("=== PAYGENT BACKFILL COMPLETED SUCCESSFULLY ===")
