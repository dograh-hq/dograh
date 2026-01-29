from datetime import UTC, datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from api.db.base_client import BaseDBClient
from api.db.models import (
    LoopTalkConversation,
    LoopTalkTestSession,
)


class LoopTalkClient(BaseDBClient):
    """Database client for LoopTalk testing operations."""

    async def create_test_session(
        self,
        organization_id: int,
        name: str,
        actor_workflow_id: int,
        adversary_workflow_id: int,
        config: Dict[str, Any],
        load_test_group_id: Optional[str] = None,
        test_index: Optional[int] = None,
    ) -> LoopTalkTestSession:
        """Create a new LoopTalk test session."""
        async with self.async_session() as session:
            test_session = LoopTalkTestSession(
                organization_id=organization_id,
                name=name,
                actor_workflow_id=actor_workflow_id,
                adversary_workflow_id=adversary_workflow_id,
                config=config,
                load_test_group_id=load_test_group_id,
                test_index=test_index,
                status="pending",
            )
            session.add(test_session)
            await session.commit()
            await session.refresh(test_session)
            return test_session

    async def get_test_session(
        self, test_session_id: int, organization_id: int
    ) -> Optional[LoopTalkTestSession]:
        """Get a test session by ID."""
        async with self.async_session() as session:
            result = await session.execute(
                select(LoopTalkTestSession)
                .options(
                    selectinload(LoopTalkTestSession.actor_workflow),
                    selectinload(LoopTalkTestSession.adversary_workflow),
                    selectinload(LoopTalkTestSession.conversations),
                )
                .where(
                    LoopTalkTestSession.id == test_session_id,
                    LoopTalkTestSession.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_test_sessions(
        self,
        organization_id: int,
        status: Optional[str] = None,
        load_test_group_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[LoopTalkTestSession]:
        """List test sessions with optional filtering."""
        async with self.async_session() as session:
            query = select(LoopTalkTestSession).where(
                LoopTalkTestSession.organization_id == organization_id
            )

            if status:
                # "active" is a virtual status used by the UI to represent
                # both "pending" and "running" sessions. Translate it into
                # the real enum values stored in the database to avoid
                # invalid enum casting errors (e.g. asyncpg InvalidTextRepresentationError).
                if status == "active":
                    query = query.where(
                        LoopTalkTestSession.status.in_(["pending", "running"])
                    )
                else:
                    query = query.where(LoopTalkTestSession.status == status)

            if load_test_group_id:
                query = query.where(
                    LoopTalkTestSession.load_test_group_id == load_test_group_id
                )

            query = (
                query.order_by(LoopTalkTestSession.created_at.desc())
                .limit(limit)
                .offset(offset)
            )

            result = await session.execute(query)
            return result.scalars().all()

    async def update_test_session_status(
        self,
        test_session_id: int,
        status: str,
        error: Optional[str] = None,
        results: Optional[Dict[str, Any]] = None,
    ) -> LoopTalkTestSession:
        """Update test session status and related fields."""
        async with self.async_session() as session:
            result = await session.execute(
                select(LoopTalkTestSession).where(
                    LoopTalkTestSession.id == test_session_id
                )
            )
            test_session = result.scalar_one()

            test_session.status = status

            if status == "running":
                test_session.started_at = datetime.now(UTC)
            elif status in ["completed", "failed"]:
                test_session.completed_at = datetime.now(UTC)

            if error:
                test_session.error = error

            if results:
                test_session.results = results

            await session.commit()
            await session.refresh(test_session)
            return test_session

    async def create_conversation(self, test_session_id: int) -> LoopTalkConversation:
        """Create a new conversation for a test session."""
        async with self.async_session() as session:
            conversation = LoopTalkConversation(test_session_id=test_session_id)
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            return conversation

    async def update_conversation(
        self,
        conversation_id: int,
        duration_seconds: Optional[int] = None,
        actor_recording_url: Optional[str] = None,
        adversary_recording_url: Optional[str] = None,
        combined_recording_url: Optional[str] = None,
        transcript: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        ended_at: Optional[datetime] = None,
    ) -> LoopTalkConversation:
        """Update conversation details."""
        async with self.async_session() as session:
            result = await session.execute(
                select(LoopTalkConversation).where(
                    LoopTalkConversation.id == conversation_id
                )
            )
            conversation = result.scalar_one()

            if duration_seconds is not None:
                conversation.duration_seconds = duration_seconds
            if actor_recording_url:
                conversation.actor_recording_url = actor_recording_url
            if adversary_recording_url:
                conversation.adversary_recording_url = adversary_recording_url
            if combined_recording_url:
                conversation.combined_recording_url = combined_recording_url
            if transcript:
                conversation.transcript = transcript
            if metrics:
                conversation.metrics = metrics
            if ended_at:
                conversation.ended_at = ended_at

            await session.commit()
            await session.refresh(conversation)
            return conversation

    # Note: Turn tracking is handled by Langfuse, not stored in our database

    async def create_load_test_group(
        self,
        organization_id: int,
        name_prefix: str,
        actor_workflow_id: int,
        adversary_workflow_id: int,
        config: Dict[str, Any],
        test_count: int,
    ) -> List[LoopTalkTestSession]:
        """Create multiple test sessions for load testing."""
        load_test_group_id = str(uuid4())
        test_sessions = []

        async with self.async_session() as session:
            for i in range(test_count):
                test_session = LoopTalkTestSession(
                    organization_id=organization_id,
                    name=f"{name_prefix} - Test {i + 1}",
                    actor_workflow_id=actor_workflow_id,
                    adversary_workflow_id=adversary_workflow_id,
                    config=config,
                    load_test_group_id=load_test_group_id,
                    test_index=i,
                    status="pending",
                )
                session.add(test_session)
                test_sessions.append(test_session)

            await session.commit()

            # Refresh all sessions
            for test_session in test_sessions:
                await session.refresh(test_session)

            return test_sessions

    async def get_load_test_group_stats(
        self, load_test_group_id: str, organization_id: int
    ) -> Dict[str, Any]:
        """Get statistics for a load test group."""
        from sqlalchemy import case, func

        async with self.async_session() as session:
            # Get status counts using SQL aggregation
            counts_result = await session.execute(
                select(
                    func.count().label("total"),
                    func.sum(
                        case((LoopTalkTestSession.status == "pending", 1), else_=0)
                    ).label("pending"),
                    func.sum(
                        case((LoopTalkTestSession.status == "running", 1), else_=0)
                    ).label("running"),
                    func.sum(
                        case((LoopTalkTestSession.status == "completed", 1), else_=0)
                    ).label("completed"),
                    func.sum(
                        case((LoopTalkTestSession.status == "failed", 1), else_=0)
                    ).label("failed"),
                ).where(
                    LoopTalkTestSession.load_test_group_id == load_test_group_id,
                    LoopTalkTestSession.organization_id == organization_id,
                )
            )
            counts = counts_result.one()

            # Get session details (still needed for the sessions list)
            sessions_result = await session.execute(
                select(
                    LoopTalkTestSession.id,
                    LoopTalkTestSession.name,
                    LoopTalkTestSession.status,
                    LoopTalkTestSession.test_index,
                    LoopTalkTestSession.created_at,
                    LoopTalkTestSession.started_at,
                    LoopTalkTestSession.completed_at,
                    LoopTalkTestSession.error,
                ).where(
                    LoopTalkTestSession.load_test_group_id == load_test_group_id,
                    LoopTalkTestSession.organization_id == organization_id,
                )
            )
            sessions = sessions_result.all()

            stats = {
                "total": counts.total or 0,
                "pending": counts.pending or 0,
                "running": counts.running or 0,
                "completed": counts.completed or 0,
                "failed": counts.failed or 0,
                "sessions": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "status": s.status,
                        "test_index": s.test_index,
                        "created_at": s.created_at,
                        "started_at": s.started_at,
                        "completed_at": s.completed_at,
                        "error": s.error,
                    }
                    for s in sessions
                ],
            }

            return stats
