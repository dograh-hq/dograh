"""Non-critical persistence orchestration for completed calls."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.memory.extraction import extract_and_store_memories
from api.tasks.function_names import FunctionNames


async def _enqueue_job(function_name: FunctionNames, workflow_run_id: int) -> None:
    from api.tasks.arq import enqueue_job

    await enqueue_job(function_name, workflow_run_id)


async def _enqueue_memory_extraction(workflow_run_id: int) -> None:
    try:
        await _enqueue_job(FunctionNames.EXTRACT_CALL_MEMORIES, workflow_run_id)
    except Exception:
        logger.warning("Unable to enqueue call memory extraction")


async def persist_call_data_with_retry(
    workflow_run_id: int,
    *,
    events: list[dict[str, Any]] | None = None,
    transcript_text: str | None = None,
) -> None:
    """Persist a final snapshot with bounded retries and an ARQ safety net."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            await db_client.persist_call_snapshot(
                workflow_run_id,
                events=events,
                transcript_text=transcript_text,
            )
            await _enqueue_memory_extraction(workflow_run_id)
            return
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.25 * (2**attempt))
    logger.error("Call snapshot persistence failed after retries for run {}", workflow_run_id)
    try:
        await _enqueue_job(FunctionNames.PERSIST_CALL_DATA, workflow_run_id)
    except Exception:
        logger.error("Call snapshot retry could not be queued for run {}", workflow_run_id)
    _ = last_error


async def persist_workflow_run_call_data(_ctx, workflow_run_id: int) -> None:
    run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if run is None:
        return
    logs = run.logs or {}
    await db_client.persist_call_snapshot(
        workflow_run_id,
        events=logs.get("realtime_feedback_events"),
        transcript_text=run.full_transcript,
    )
    await _enqueue_memory_extraction(workflow_run_id)


async def extract_workflow_run_memories(_ctx, workflow_run_id: int) -> None:
    await extract_and_store_memories(workflow_run_id)
