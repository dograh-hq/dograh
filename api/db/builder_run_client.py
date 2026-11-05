"""Organisation-scoped durable builder admission, progress and worker ownership."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update

from api.db.base_client import BaseDBClient
from api.db.models import BuilderRunEventModel, BuilderRunModel
from api.db.workflow_builder import BuilderBusy

ACTIVE = ("queued", "running")


def _run(row: BuilderRunModel) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class BuilderRunClient(BaseDBClient):
    async def admit_builder_run(
        self, *, organization_id: int, user_id: int, session_id: str, request: dict
    ) -> dict:
        async with self.async_session() as session:
            # A request ID is global; serialize cross-session collisions too.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"builder-request:{request['request_id']}"},
            )
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"builder-admission:{organization_id}:{user_id}:{session_id}"},
            )
            existing = await session.get(BuilderRunModel, request["request_id"])
            if existing:
                if (
                    existing.organization_id,
                    existing.user_id,
                    existing.session_id,
                ) != (organization_id, user_id, session_id):
                    raise ValueError("Request ID belongs to another conversation")
                return _run(existing)
            active = await session.scalar(
                select(BuilderRunModel.id).where(
                    BuilderRunModel.organization_id == organization_id,
                    BuilderRunModel.user_id == user_id,
                    BuilderRunModel.session_id == session_id,
                    BuilderRunModel.status.in_(ACTIVE),
                )
            )
            if active:
                raise BuilderBusy("This conversation is already running")
            row = BuilderRunModel(
                id=request["request_id"],
                organization_id=organization_id,
                user_id=user_id,
                session_id=session_id,
                request=request,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _run(row)

    async def get_builder_run(
        self, run_id: str, *, organization_id: int, user_id: int, session_id: str
    ) -> dict | None:
        async with self.async_session() as session:
            row = await session.scalar(
                select(BuilderRunModel).where(
                    BuilderRunModel.id == run_id,
                    BuilderRunModel.organization_id == organization_id,
                    BuilderRunModel.user_id == user_id,
                    BuilderRunModel.session_id == session_id,
                )
            )
            return _run(row) if row else None

    async def latest_builder_run(
        self, *, organization_id: int, user_id: int, session_id: str
    ) -> dict | None:
        async with self.async_session() as session:
            row = await session.scalar(
                select(BuilderRunModel)
                .where(
                    BuilderRunModel.organization_id == organization_id,
                    BuilderRunModel.user_id == user_id,
                    BuilderRunModel.session_id == session_id,
                )
                .order_by(BuilderRunModel.created_at.desc())
                .limit(1)
            )
            return _run(row) if row else None

    @asynccontextmanager
    async def claim_builder_run(self, run_id: str):
        """System worker only: a PG session lock excludes duplicate job delivery."""
        async with self.engine.connect() as connection:
            # A run holds this connection through inference. Detach it so ten
            # ARQ jobs cannot consume the shared pool's slots needed for their
            # own checkpoint, progress and tool queries. Context exit closes it.
            await connection.run_sync(lambda sync: sync.detach())
            acquired = False
            try:
                try:
                    acquired = await connection.scalar(
                        text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                        {"key": f"builder-run:{run_id}"},
                    )
                except BaseException:
                    # A cancelled query may have acquired the lock on the server.
                    # Never return that connection to the pool.
                    await connection.invalidate()
                    raise
                await connection.commit()
                if not acquired:
                    yield None
                    return
                async with self.async_session() as session:
                    row = await session.get(BuilderRunModel, run_id)
                    if not row or row.status not in ACTIVE:
                        selected = None
                    else:
                        row.status = "running"
                        row.attempts += 1
                        row.updated_at = datetime.now(UTC)
                        await session.commit()
                        await session.refresh(row)
                        selected = _run(row)
                yield selected
            finally:
                if acquired and not connection.invalidated:
                    try:
                        await connection.execute(
                            text(
                                "SELECT pg_advisory_unlock(hashtextextended(:key, 0))"
                            ),
                            {"key": f"builder-run:{run_id}"},
                        )
                        await connection.commit()
                    except BaseException:
                        await connection.invalidate()
                        raise

    async def append_builder_event(
        self, run_id: str, event: dict, *, status: str | None = None
    ) -> int:
        """Worker-owned write. Event and terminal status commit together."""
        async with self.async_session() as session:
            row = await session.scalar(
                select(BuilderRunModel)
                .where(BuilderRunModel.id == run_id)
                .with_for_update()
            )
            if row is None:
                raise ValueError("Builder run not found")
            seq = (
                await session.scalar(
                    select(func.max(BuilderRunEventModel.seq)).where(
                        BuilderRunEventModel.run_id == run_id
                    )
                )
                or 0
            ) + 1
            session.add(BuilderRunEventModel(run_id=run_id, seq=seq, payload=event))
            row.updated_at = datetime.now(UTC)
            if status is not None:
                row.status = status
            await session.commit()
            return seq

    async def builder_run_events(
        self,
        run_id: str,
        *,
        organization_id: int,
        user_id: int,
        session_id: str,
        after: int = 0,
    ) -> list[dict]:
        async with self.async_session() as session:
            rows = (
                await session.execute(
                    select(BuilderRunEventModel.seq, BuilderRunEventModel.payload)
                    .join(
                        BuilderRunModel,
                        BuilderRunModel.id == BuilderRunEventModel.run_id,
                    )
                    .where(
                        BuilderRunModel.id == run_id,
                        BuilderRunModel.organization_id == organization_id,
                        BuilderRunModel.user_id == user_id,
                        BuilderRunModel.session_id == session_id,
                        BuilderRunEventModel.seq > after,
                    )
                    .order_by(BuilderRunEventModel.seq)
                    .limit(50)
                )
            ).all()
            return [{"seq": row.seq, "event": row.payload} for row in rows]

    async def cancel_builder_run(
        self, run_id: str, *, organization_id: int, user_id: int, session_id: str
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                update(BuilderRunModel)
                .where(
                    BuilderRunModel.id == run_id,
                    BuilderRunModel.organization_id == organization_id,
                    BuilderRunModel.user_id == user_id,
                    BuilderRunModel.session_id == session_id,
                    BuilderRunModel.status.in_(ACTIVE),
                )
                .values(cancel_requested=True)
            )
            await session.commit()
            return bool(result.rowcount)

    async def recoverable_builder_runs(self) -> list[str]:
        """System sweep: re-wake queued jobs and executions abandoned by a worker."""
        async with self.async_session() as session:
            return list(
                (
                    await session.scalars(
                        select(BuilderRunModel.id)
                        .where(
                            BuilderRunModel.status.in_(ACTIVE),
                            BuilderRunModel.updated_at
                            < datetime.now(UTC) - timedelta(minutes=1),
                        )
                        .order_by(BuilderRunModel.updated_at)
                        .limit(100)
                    )
                ).all()
            )

    async def heartbeat_builder_run(self, run_id: str) -> bool:
        async with self.async_session() as session:
            cancelled = await session.scalar(
                update(BuilderRunModel)
                .where(BuilderRunModel.id == run_id, BuilderRunModel.status.in_(ACTIVE))
                .values(updated_at=datetime.now(UTC))
                .returning(BuilderRunModel.cancel_requested)
            )
            await session.commit()
            return bool(cancelled)
