import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

import alembic_postgresql_enum  # noqa: F401 - registers enum handling hooks
from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from api.constants import DATABASE_URL

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Interpret the config file for Python logging.
config = context.config
fileConfig(config.config_file_name)

# Import your model's MetaData object for 'autogenerate' support.
from api.db.models import Base  # noqa: E402 ensure this points to your models.py

target_metadata = Base.metadata


def get_url():
    """Get database URL from environment variable or config file."""
    return DATABASE_URL or config.get_main_option("sqlalchemy.url")


def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        render_item=render_item,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def include_object(object, name, type_, reflected, compare_to):
    """
    Custom function to exclude redundant indexes on primary keys.
    """
    # Unused parameters are required by Alembic's API
    _ = (name, reflected, compare_to)

    if type_ == "index":
        # Skip indexes on primary key columns that aren't unique
        # Primary keys already have implicit unique indexes
        if hasattr(object, "columns") and len(object.columns) == 1:
            col = list(object.columns)[0]
            if col.primary_key and not object.unique:
                return False
    return True


def render_item(type_, obj, autogen_context):
    """
    Custom render function to fix index generation.
    """
    # Unused parameter is required by Alembic's API
    _ = autogen_context

    if type_ == "index":
        # For indexes on columns marked as unique in the model,
        # ensure the index is also unique
        if hasattr(obj, "columns"):
            for col in obj.columns:
                if hasattr(col, "unique") and col.unique and not obj.unique:
                    obj.unique = True
                    break
    return False  # Let Alembic handle the rendering


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_item=render_item,
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    """Run migrations in 'online' mode."""
    connectable = create_async_engine(
        get_url(),
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # Run migrations using the synchronous 'do_run_migrations' function
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
