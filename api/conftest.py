"""
Pytest configuration and fixtures for async database testing.

This module sets up the test infrastructure using:
- A separate test database (appends _test to the database name)
- Alembic migrations run once per test session
- Transaction-based isolation for each test (savepoint pattern)

References:
- https://www.core27.co/post/transactional-unit-tests-with-pytest-and-async-sqlalchemy
- https://docs.sqlalchemy.org/en/20/orm/session_transaction.html
"""

import os
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import urlparse, urlunparse

# Load environment variables before importing anything else
from dotenv import load_dotenv

# Load .env.test from api directory for test configuration
env_path = Path(__file__).parent / ".env.test"
load_dotenv(env_path)

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import SessionTransaction
from sqlalchemy.pool import NullPool


def get_test_database_url() -> str:
    """
    Get the test database URL by appending _test to the database name.

    Example:
        postgresql+asyncpg://user:pass@host/mydb
        -> postgresql+asyncpg://user:pass@host/mydb_test
    """
    original_url = os.environ.get("DATABASE_URL")
    if not original_url:
        raise ValueError("DATABASE_URL environment variable is not set")

    parsed = urlparse(original_url)
    # Append _test to the database name (path without leading slash)
    original_db_name = parsed.path.lstrip("/")
    test_db_name = f"{original_db_name}_test"

    # Reconstruct the URL with the new database name
    test_url = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{test_db_name}",
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
    return test_url


def get_base_database_url() -> str:
    """
    Get base database URL (postgres) for creating/dropping test database.
    """
    original_url = os.environ.get("DATABASE_URL")
    parsed = urlparse(original_url)
    # Connect to 'postgres' database for admin operations
    base_url = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            "/postgres",
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
    return base_url


def get_test_db_name() -> str:
    """Extract the test database name."""
    original_url = os.environ.get("DATABASE_URL")
    parsed = urlparse(original_url)
    original_db_name = parsed.path.lstrip("/")
    return f"{original_db_name}_test"


@pytest.fixture(scope="session")
async def setup_test_database():
    """
    Session-scoped fixture that creates the test database and runs migrations.

    This runs once at the start of the test session.
    """
    test_db_name = get_test_db_name()
    base_url = get_base_database_url()
    test_url = get_test_database_url()

    # Create engine to connect to postgres database (for admin operations)
    admin_engine = create_async_engine(
        base_url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",  # Required for CREATE DATABASE
    )

    # Create test database if it doesn't exist
    async with admin_engine.connect() as conn:
        # Check if database exists
        result = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
            {"dbname": test_db_name},
        )
        exists = result.scalar() is not None

        if not exists:
            print(f"\n Creating test database: {test_db_name}")
            # Use template0 to avoid collation version mismatch issues
            await conn.execute(
                text(f'CREATE DATABASE "{test_db_name}" TEMPLATE template0')
            )
        else:
            print(f"\n Using existing test database: {test_db_name}")

    await admin_engine.dispose()

    # Run alembic migrations on the test database
    print(f" Running migrations on {test_db_name}...")
    await run_migrations(test_url)
    print(" Migrations complete!")

    yield test_url

    # Cleanup: Optionally drop the test database after tests
    # Commented out to allow inspection of test data after failures
    # async with admin_engine.connect() as conn:
    #     await conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}"'))


async def run_migrations(database_url: str):
    """
    Run alembic migrations programmatically on the given database.
    """
    from alembic import command
    from alembic.config import Config

    # Get alembic.ini path
    alembic_ini_path = Path(__file__).parent / "alembic.ini"

    # Create alembic config
    alembic_cfg = Config(str(alembic_ini_path))

    # Override the database URL - need to patch both os.environ AND api.constants
    # because api.constants.DATABASE_URL is cached at import time
    original_env_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)

    # Also patch the cached value in api.constants
    import api.constants

    original_constants_url = api.constants.DATABASE_URL

    api.constants.DATABASE_URL = database_url

    # Run migrations in a thread to avoid blocking the event loop
    import asyncio

    def _run_upgrade():
        command.upgrade(alembic_cfg, "head")

    try:
        await asyncio.get_event_loop().run_in_executor(None, _run_upgrade)
    finally:
        # Restore original DATABASE_URL
        if original_env_url:
            os.environ["DATABASE_URL"] = original_env_url
        api.constants.DATABASE_URL = original_constants_url


@pytest.fixture(scope="session")
async def test_engine(setup_test_database):
    """
    Create a test database engine (session-scoped).

    Uses NullPool to avoid connection issues with async tests.
    """
    test_url = setup_test_database
    engine = create_async_engine(
        test_url,
        poolclass=NullPool,
        echo=False,  # Set to True for SQL debugging
    )
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
async def db_connection(test_engine) -> AsyncGenerator[AsyncConnection, None]:
    """
    Create a database connection for each test.

    This connection wraps all operations in a transaction that
    will be rolled back at the end of the test.
    """
    async with test_engine.connect() as connection:
        yield connection


@pytest.fixture(scope="function")
async def async_session(
    db_connection: AsyncConnection,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Create a database session with transaction isolation for each test.

    This fixture:
    1. Begins a transaction on the connection
    2. Creates a savepoint (nested transaction)
    3. Yields the session for test use
    4. Rolls back all changes after the test

    Tests can call session.commit() and it will only commit to the savepoint,
    not to the actual database. The outer transaction rollback ensures
    complete isolation between tests.
    """
    # Begin outer transaction
    trans = await db_connection.begin()

    # Create session bound to this connection
    async_session_maker = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
    )

    async with async_session_maker() as session:
        # Begin a nested transaction (savepoint)
        nested = await session.begin_nested()

        # Set up event listener to restart savepoint after commits
        @event.listens_for(session.sync_session, "after_transaction_end")
        def reopen_nested_transaction(session_sync, transaction: SessionTransaction):
            nonlocal nested
            if not nested.is_active:
                nested = session.sync_session.begin_nested()

        yield session

        # Rollback everything
        await trans.rollback()


class _TestSessionContext:
    """
    Context manager wrapper for test session.

    Mimics the behavior of async_sessionmaker() context manager
    but uses the existing test session without closing it.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            await self._session.flush()
        return False


@pytest.fixture(scope="function")
async def db_session(async_session: AsyncSession):
    """
    Create a DBClient instance that uses the test session.

    This patches the DBClient's async_session to use our test session,
    ensuring all database operations go through the transactional test session.

    Note: This fixture yields a DBClient (not a raw session) for backward
    compatibility with existing tests that call db_session.get_or_create_user_by_provider_id(), etc.
    """
    from api.db import db_client

    def test_session_maker():
        return _TestSessionContext(async_session)

    # Store originals
    original_engine = db_client.engine
    original_async_session = db_client.async_session

    # Patch the db_client to use our test session
    db_client.async_session = test_session_maker

    yield db_client

    # Restore originals
    db_client.engine = original_engine
    db_client.async_session = original_async_session


@pytest.fixture
async def test_client_factory(db_session):
    """
    Factory fixture that creates test clients for specific users.
    This allows tests to create custom users and test clients on demand.

    Usage:
        async def test_something(test_client_factory, db_session):
            # Create a custom user
            user = await db_session.get_or_create_user_by_provider_id("custom_user_123")

            # Create a test client for this user
            async with test_client_factory(user) as client:
                # Use the client in your test
                response = await client.get("/some/endpoint")
    """
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from api.app import app
    from api.services.auth.depends import get_user

    @asynccontextmanager
    async def _create_client_for_user(user):
        # Create mock auth dependency for this user
        async def mock_get_user():
            return user

        # Override the dependency
        original_override = app.dependency_overrides.get(get_user)
        app.dependency_overrides[get_user] = mock_get_user

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
        finally:
            # Clean up the override
            if original_override:
                app.dependency_overrides[get_user] = original_override
            else:
                app.dependency_overrides.pop(get_user, None)

    return _create_client_for_user
