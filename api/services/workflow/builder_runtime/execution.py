"""Durable builder execution. HTTP clients admit work and subscribe to its events."""

import asyncio
from contextlib import aclosing
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.db.workflow_builder import BuilderBusy
from api.schemas.workflow_builder import BuilderSession, BuilderTurnRequest

ACTIVE = {"queued", "running"}


def _scope(user, session_id) -> dict:
    return {
        "organization_id": user.selected_organization_id,
        "user_id": user.id,
        "session_id": str(session_id),
    }


async def enqueue_builder_run(run_id: str) -> None:
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    # The database owns admission and exclusion. A fresh wake is safe even if an
    # ARQ result from an interrupted earlier attempt is still retained in Redis.
    await enqueue_job(
        FunctionNames.PROCESS_BUILDER_RUN, run_id, _job_id=f"builder:{run_id}:{uuid4()}"
    )


async def admit_builder_turn(
    user, session_id: UUID, request: BuilderTurnRequest
) -> dict:
    if not user.selected_organization_id:
        raise HTTPException(403, "Select an organization first")
    if request.workflow_id is not None:
        from api.services.workflow.builder import load_builder_workflow

        await load_builder_workflow(user, session_id, request.workflow_id)
    try:
        run = await db_client.admit_builder_run(
            **_scope(user, session_id), request=request.model_dump(mode="json")
        )
    except BuilderBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if run["status"] in ACTIVE:
        try:
            await enqueue_builder_run(run["id"])
        except Exception:  # noqa: BLE001 -- persist a safe error at the worker/queue boundary
            # The periodic sweep recovers a committed admission after a Redis outage.
            logger.exception(
                "Builder request {} is durable but could not be queued", run["id"]
            )
    return run


async def get_run(user, session_id: UUID, run_id: UUID | str) -> dict:
    run = await db_client.get_builder_run(str(run_id), **_scope(user, session_id))
    if run is None:
        raise HTTPException(404, "Builder run not found")
    return run


async def stream_builder_run(user, session_id: UUID, run_id: str, after: int = 0):
    from api.services.workflow.builder import encode_event

    run = await get_run(user, session_id, run_id)
    yield encode_event({"type": "run", "run_id": run_id})
    if run["status"] == "queued":
        yield encode_event(
            {
                "type": "status",
                "message": "Your request is queued. Waiting for the builder to start…",
            }
        )
    quiet = 0
    while True:
        rows = await db_client.builder_run_events(
            run_id, **_scope(user, session_id), after=after
        )
        for row in rows:
            after = row["seq"]
            yield f"id: {after}\n" + encode_event({**row["event"], "seq": after})
        if rows:
            quiet = 0
            continue
        run = await get_run(user, session_id, run_id)
        if run["status"] not in ACTIVE:
            # Re-read after observing terminal status: the final event and status
            # commit together, but may have committed after the previous query.
            final = await db_client.builder_run_events(
                run_id, **_scope(user, session_id), after=after
            )
            for row in final:
                after = row["seq"]
                yield f"id: {after}\n" + encode_event({**row["event"], "seq": after})
            if final:
                continue
            return
        await asyncio.sleep(0.5)
        quiet += 1
        if quiet % 30 == 0:
            if run["status"] == "queued":
                yield encode_event(
                    {
                        "type": "status",
                        "message": "Your request is saved and still waiting for the builder to start. You can wait or stop it.",
                    }
                )
            else:
                yield ": heartbeat\n\n"


async def session_with_execution(
    user, session_id: UUID, session: BuilderSession | None = None
) -> BuilderSession:
    from api.services.workflow.builder import load_session

    run = await db_client.latest_builder_run(**_scope(user, session_id))
    try:
        session = session or await load_session(user, session_id)
    except HTTPException as exc:
        if exc.status_code != 404 or run is None:
            raise
        session = BuilderSession(id=session_id)
    if run:
        session.active_run_id = UUID(run["id"]) if run["status"] in ACTIVE else None
        session.run_status = run["status"]
    return session


async def cancel_run(user, session_id: UUID, run_id: UUID) -> dict:
    await get_run(user, session_id, run_id)
    requested = await db_client.cancel_builder_run(
        str(run_id), **_scope(user, session_id)
    )
    return {"cancel_requested": requested}


async def execute_builder_run(run_id: str) -> None:
    """ARQ entry: identity and work come from admitted data, never job arguments."""
    from api.services.workflow.builder import (
        builder_turn,
        load_session,
        save_builder_draft,
    )

    async with db_client.claim_builder_run(run_id) as run:
        if run is None:
            return
        if run["attempts"] > 3:
            await db_client.append_builder_event(
                run_id,
                {
                    "type": "error",
                    "message": "Recovery stopped after three attempts. Reopen the conversation to continue.",
                },
                status="failed",
            )
            return

        user = await db_client.get_user_by_id(run["user_id"])
        if user is None or not await db_client.is_user_member_of_organization(
            run["user_id"], run["organization_id"]
        ):
            await db_client.append_builder_event(
                run_id,
                {
                    "type": "error",
                    "message": "Organisation access is no longer available.",
                },
                status="failed",
            )
            return
        actor = SimpleNamespace(
            id=user.id,
            provider_id=user.provider_id,
            selected_organization_id=run["organization_id"],
        )
        session_id = UUID(run["session_id"])
        request = BuilderTurnRequest.model_validate(run["request"])

        async def stopped():
            try:
                session = await load_session(actor, session_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            else:
                await db_client.append_builder_event(
                    run_id,
                    {"type": "session", "session": session.model_dump(mode="json")},
                )
            await db_client.append_builder_event(
                run_id,
                {
                    "type": "cancelled",
                    "message": "Stopped. Your working copy is preserved.",
                },
                status="cancelled",
            )

        if run["cancel_requested"]:
            await stopped()
            return

        async def work():
            current = None
            if run["attempts"] > 1:
                try:
                    current = await load_session(actor, session_id)
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                if current is not None and current.checkpoint != request.checkpoint:
                    # An admitted request already advanced the graph. Continue its
                    # durable state instead of appending the original message twice.
                    if current.pending and any(
                        p.kind != "mcp" for p in current.pending
                    ):
                        await db_client.append_builder_event(
                            run_id,
                            {
                                "type": "session",
                                "session": current.model_dump(mode="json"),
                            },
                        )
                        await db_client.append_builder_event(
                            run_id, {"type": "done"}, status="completed"
                        )
                        return
                    if not current.can_continue:
                        await finish(current)
                        return
                    resumed = BuilderTurnRequest(
                        request_id=uuid5(UUID(run_id), current.checkpoint),
                        checkpoint=current.checkpoint,
                        workflow_id=request.workflow_id,
                    )
                else:
                    resumed = request
            else:
                resumed = request
            latest = current
            async with aclosing(builder_turn(actor, session_id, resumed)) as events:
                async for event in events:
                    if event["type"] == "session":
                        latest = BuilderSession.model_validate(event["session"])
                    if event["type"] == "done":
                        if latest is not None:
                            await finish(latest)
                        else:
                            await db_client.append_builder_event(
                                run_id, event, status="completed"
                            )
                        continue
                    if event["type"] == "error":
                        await db_client.append_builder_event(
                            run_id, event, status="failed"
                        )
                        continue
                    await db_client.append_builder_event(run_id, event)

        async def finish(session: BuilderSession):
            if (
                request.workflow_id is not None
                and session.proposal
                and not session.can_continue
                and not session.pending
            ):
                await db_client.append_builder_event(
                    run_id, {"type": "status", "message": "Updating your agent…"}
                )
                saved = await save_builder_draft(
                    actor, session_id, session.checkpoint, request.workflow_id
                )
                await db_client.append_builder_event(
                    run_id, {"type": "saved", "workflow_id": saved}
                )
            await db_client.append_builder_event(
                run_id, {"type": "session", "session": session.model_dump(mode="json")}
            )
            await db_client.append_builder_event(
                run_id, {"type": "done"}, status="completed"
            )

        task = asyncio.create_task(work())
        try:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=1)
                if done:
                    break
                if await db_client.heartbeat_builder_run(run_id):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await stopped()
                    return
            await task
        except asyncio.CancelledError:
            # Worker shutdown preserves the active row for recovery; user Stop is
            # handled above. Cancellation never implies the request was completed.
            raise
        except HTTPException as exc:
            await db_client.append_builder_event(
                run_id,
                {
                    "type": "error",
                    "message": str(exc.detail),
                    "status_code": exc.status_code,
                },
                status="failed",
            )
        except Exception:  # noqa: BLE001 -- persist a safe error at the worker/queue boundary
            logger.exception("Builder run {} failed", run_id)
            await db_client.append_builder_event(
                run_id,
                {
                    "type": "error",
                    "message": "The builder stopped unexpectedly. Reopen the conversation to continue.",
                },
                status="failed",
            )
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
