"""Async connection pool to the Asterisk Realtime Architecture Postgres.

Lives separate from Dograh's primary SQLAlchemy engine because the ARA
Postgres is operationally distinct (Asterisk-owned schema, typically a
different host, different credentials). DSN comes from the
``ASTERISK_ARA_DSN`` environment variable.
"""

from __future__ import annotations

import os
from typing import Optional

import asyncpg
from loguru import logger

_POOL: Optional[asyncpg.Pool] = None
_DSN_ENV = "ASTERISK_ARA_DSN"


class AraNotConfiguredError(RuntimeError):
    """Raised when ASTERISK_ARA_DSN is missing.

    The 3CX provider can't provision its trunk without an ARA Postgres to
    write to — callers translate this into a user-visible HTTP 400 with a
    pointer to docs/providers/three_cx.md.
    """


async def get_pool() -> asyncpg.Pool:
    """Return the lazily-initialised ARA pool. Idempotent across awaits."""
    global _POOL
    if _POOL is not None:
        return _POOL

    dsn = os.getenv(_DSN_ENV)
    if not dsn:
        raise AraNotConfiguredError(
            f"{_DSN_ENV} not set — 3CX provider needs an Asterisk Realtime "
            f"Postgres DSN to provision the PJSIP trunk. See "
            f"docs/providers/three_cx.md for setup."
        )

    logger.info(f"[3CX/ARA] opening asyncpg pool to {_dsn_for_log(dsn)}")
    _POOL = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=4,
        command_timeout=10,
    )
    return _POOL


async def close_pool() -> None:
    """Close the pool — exposed for test teardown and graceful shutdown."""
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


def _dsn_for_log(dsn: str) -> str:
    """Strip the password from a DSN before logging it."""
    if "@" not in dsn or "://" not in dsn:
        return "<dsn>"
    scheme, rest = dsn.split("://", 1)
    creds, host = rest.split("@", 1)
    user = creds.split(":", 1)[0] if ":" in creds else creds
    return f"{scheme}://{user}:***@{host}"
