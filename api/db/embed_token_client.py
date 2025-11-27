"""Database client for managing embed tokens and sessions."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import List, Optional

from loguru import logger
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.base_client import BaseDBClient
from api.db.models import EmbedSessionModel, EmbedTokenModel


class EmbedTokenClient(BaseDBClient):
    """Client for managing embed tokens and sessions."""

    async def create_embed_token(
        self,
        workflow_id: int,
        organization_id: int,
        created_by: int,
        allowed_domains: Optional[List[str]] = None,
        settings: Optional[dict] = None,
        usage_limit: Optional[int] = None,
        expires_at: Optional[datetime] = None,
    ) -> EmbedTokenModel:
        """Create a new embed token for a workflow.

        Args:
            workflow_id: ID of the workflow to embed
            organization_id: ID of the organization
            created_by: ID of the user creating the token
            allowed_domains: List of domains allowed to use this token
            settings: Widget customization settings
            usage_limit: Optional limit on number of uses
            expires_at: Optional expiration date

        Returns:
            Created EmbedTokenModel
        """
        async with self.async_session() as session:
            # Generate a unique token
            token = f"emb_{secrets.token_urlsafe(32)}"

            # Ensure uniqueness
            while await self._token_exists(session, token):
                token = f"emb_{secrets.token_urlsafe(32)}"

            embed_token = EmbedTokenModel(
                token=token,
                workflow_id=workflow_id,
                organization_id=organization_id,
                created_by=created_by,
                allowed_domains=allowed_domains,
                settings=settings or {},
                usage_limit=usage_limit,
                expires_at=expires_at,
                is_active=True,
                usage_count=0,
                created_at=datetime.now(UTC),
            )

            session.add(embed_token)
            await session.commit()
            await session.refresh(embed_token)

            logger.info(f"Created embed token {token} for workflow {workflow_id}")
            return embed_token

    async def _token_exists(self, session: AsyncSession, token: str) -> bool:
        """Check if a token already exists."""
        result = await session.execute(
            select(EmbedTokenModel).where(EmbedTokenModel.token == token)
        )
        return result.scalar_one_or_none() is not None

    async def get_embed_token_by_token(self, token: str) -> Optional[EmbedTokenModel]:
        """Get an embed token by its token string.

        Args:
            token: The token string

        Returns:
            EmbedTokenModel if found, None otherwise
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(EmbedTokenModel).where(EmbedTokenModel.token == token)
            )
            return result.scalar_one_or_none()

    async def get_embed_tokens_by_workflow(
        self, workflow_id: int, organization_id: int, active_only: bool = True
    ) -> List[EmbedTokenModel]:
        """Get all embed tokens for a workflow.

        Args:
            workflow_id: ID of the workflow
            organization_id: ID of the organization
            active_only: If True, only return active tokens

        Returns:
            List of EmbedTokenModel instances
        """
        async with self.async_session() as session:
            query = select(EmbedTokenModel).where(
                and_(
                    EmbedTokenModel.workflow_id == workflow_id,
                    EmbedTokenModel.organization_id == organization_id,
                )
            )

            if active_only:
                query = query.where(EmbedTokenModel.is_active == True)

            result = await session.execute(
                query.order_by(EmbedTokenModel.created_at.desc())
            )
            return result.scalars().all()

    async def update_embed_token(
        self, token_id: int, organization_id: int, **kwargs
    ) -> Optional[EmbedTokenModel]:
        """Update an embed token.

        Args:
            token_id: ID of the token to update
            organization_id: ID of the organization (for access control)
            **kwargs: Fields to update (allowed_domains, settings, is_active, etc.)

        Returns:
            Updated EmbedTokenModel if found, None otherwise
        """
        async with self.async_session() as session:
            # First get the token to verify organization
            result = await session.execute(
                select(EmbedTokenModel).where(
                    and_(
                        EmbedTokenModel.id == token_id,
                        EmbedTokenModel.organization_id == organization_id,
                    )
                )
            )
            embed_token = result.scalar_one_or_none()

            if not embed_token:
                return None

            # Update allowed fields
            allowed_fields = {
                "allowed_domains",
                "settings",
                "is_active",
                "usage_limit",
                "expires_at",
            }

            for field, value in kwargs.items():
                if field in allowed_fields:
                    setattr(embed_token, field, value)

            embed_token.updated_at = datetime.now(UTC)

            await session.commit()
            await session.refresh(embed_token)

            logger.info(f"Updated embed token {token_id}")
            return embed_token

    async def deactivate_embed_token(self, token_id: int, organization_id: int) -> bool:
        """Deactivate an embed token.

        Args:
            token_id: ID of the token to deactivate
            organization_id: ID of the organization

        Returns:
            True if token was deactivated, False if not found
        """
        token = await self.update_embed_token(
            token_id, organization_id, is_active=False
        )
        return token is not None

    async def increment_embed_token_usage(self, token_id: int) -> None:
        """Increment the usage count for an embed token.

        Args:
            token_id: ID of the token
        """
        async with self.async_session() as session:
            await session.execute(
                update(EmbedTokenModel)
                .where(EmbedTokenModel.id == token_id)
                .values(usage_count=EmbedTokenModel.usage_count + 1)
            )
            await session.commit()

    async def create_embed_session(
        self,
        session_token: str,
        embed_token_id: int,
        workflow_run_id: int,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        origin: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> EmbedSessionModel:
        """Create a new embed session.

        Args:
            session_token: Unique session token
            embed_token_id: ID of the embed token
            workflow_run_id: ID of the workflow run
            client_ip: Client IP address
            user_agent: User agent string
            origin: Origin header
            expires_at: Session expiration time

        Returns:
            Created EmbedSessionModel
        """
        async with self.async_session() as session:
            if expires_at is None:
                expires_at = datetime.now(UTC) + timedelta(hours=1)

            embed_session = EmbedSessionModel(
                session_token=session_token,
                embed_token_id=embed_token_id,
                workflow_run_id=workflow_run_id,
                client_ip=client_ip,
                user_agent=user_agent,
                origin=origin,
                created_at=datetime.now(UTC),
                expires_at=expires_at,
            )

            session.add(embed_session)
            await session.commit()
            await session.refresh(embed_session)

            logger.info(f"Created embed session {session_token}")
            return embed_session

    async def get_embed_session_by_token(
        self, session_token: str
    ) -> Optional[EmbedSessionModel]:
        """Get an embed session by token (alias for get_embed_session).

        Args:
            session_token: The session token

        Returns:
            EmbedSessionModel if found, None otherwise (doesn't check expiry)
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(EmbedSessionModel).where(
                    EmbedSessionModel.session_token == session_token
                )
            )
            return result.scalar_one_or_none()

    async def get_embed_token_by_id(self, token_id: int) -> Optional[EmbedTokenModel]:
        """Get an embed token by ID.

        Args:
            token_id: ID of the token

        Returns:
            EmbedTokenModel if found, None otherwise
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(EmbedTokenModel).where(EmbedTokenModel.id == token_id)
            )
            return result.scalar_one_or_none()

    async def get_embed_token_stats(self, token_id: int, organization_id: int) -> dict:
        """Get usage statistics for an embed token.

        Args:
            token_id: ID of the token
            organization_id: ID of the organization

        Returns:
            Dictionary with usage statistics
        """
        async with self.async_session() as session:
            # Get the token
            result = await session.execute(
                select(EmbedTokenModel).where(
                    and_(
                        EmbedTokenModel.id == token_id,
                        EmbedTokenModel.organization_id == organization_id,
                    )
                )
            )
            token = result.scalar_one_or_none()

            if not token:
                return {}

            # Count active sessions
            active_sessions_result = await session.execute(
                select(EmbedSessionModel).where(
                    and_(
                        EmbedSessionModel.embed_token_id == token_id,
                        EmbedSessionModel.expires_at > datetime.now(UTC),
                    )
                )
            )
            active_sessions = len(active_sessions_result.scalars().all())

            return {
                "token_id": token_id,
                "usage_count": token.usage_count,
                "usage_limit": token.usage_limit,
                "active_sessions": active_sessions,
                "is_active": token.is_active,
                "created_at": token.created_at.isoformat()
                if token.created_at
                else None,
                "expires_at": token.expires_at.isoformat()
                if token.expires_at
                else None,
                "allowed_domains": token.allowed_domains,
            }
