from api.constants import normalize_async_pg_url


def test_normalize_async_pg_url():
    # Managed providers hand out these schemes → rewrite to asyncpg.
    assert (
        normalize_async_pg_url("postgres://u:p@host:5432/db")
        == "postgresql+asyncpg://u:p@host:5432/db"
    )
    assert (
        normalize_async_pg_url("postgresql://u:p@host:5432/db")
        == "postgresql+asyncpg://u:p@host:5432/db"
    )
    # Already-qualified URL is left untouched (no double prefix).
    already = "postgresql+asyncpg://u:p@host:5432/db"
    assert normalize_async_pg_url(already) == already
