"""Opt-in evidence test for the real Explorer database role.

Set DATA_EXPLORER_PERMISSION_TEST_URL only to a disposable/test PostgreSQL
database that has the CALMOS schema. This test never performs a mutation; it
uses PostgreSQL's privilege predicates plus the repository's read-only
transaction setting as the proof.
"""

import os

import pytest

from data_explorer.repository import ExplorerRepository


@pytest.mark.asyncio
async def test_readonly_database_role_has_no_mutation_privileges():
    database_url = os.getenv("DATA_EXPLORER_PERMISSION_TEST_URL")
    if not database_url:
        pytest.skip("set DATA_EXPLORER_PERMISSION_TEST_URL for real PostgreSQL permission evidence")
    repository = ExplorerRepository(database_url)
    try:
        status = await repository.readonly_status()
    finally:
        await repository.close()
    assert status["transaction_read_only"] == "on"
    assert status["can_insert"] is False
    assert status["can_update"] is False
    assert status["can_delete"] is False
    assert status["can_create"] is False
