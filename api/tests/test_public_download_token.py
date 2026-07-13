"""Tests for public download token expiry, rotation and revocation (issue #329).

Two groups:

- ``TestPublicTokenLifecycle`` — DB integration tests (transactional test
  session) exercising the real SQL freshness filter, rotation and the
  org-scoped revoke join.
- The route-wiring tests at the bottom run without a database; they assert the
  endpoints translate the DB-client results into the right HTTP behavior.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.db.models import (
    OrganizationModel,
    UserModel,
    WorkflowRunModel,
)

GRAPH = {
    "nodes": [
        {"id": "1", "type": "startCall", "data": {"name": "Start", "prompt": "Hi"}},
        {"id": "2", "type": "endCall", "data": {"name": "End", "prompt": "Bye"}},
    ],
    "edges": [{"id": "e1", "source": "1", "target": "2", "data": {"label": "End"}}],
}


# ---------------------------------------------------------------------------
# DB integration
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_run(db_session, async_session):
    """Create org → user → workflow → run in the transactional test session."""
    org = OrganizationModel(provider_id="test-org-pubtoken")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(provider_id="test-user-pubtoken", selected_organization_id=org.id)
    async_session.add(user)
    await async_session.flush()

    workflow = await db_session.create_workflow(
        name="Token WF",
        workflow_definition=GRAPH,
        user_id=user.id,
        organization_id=org.id,
    )
    run = await db_session.create_workflow_run(
        name="run-1",
        workflow_id=workflow.id,
        mode="webrtc",
        user_id=user.id,
        organization_id=org.id,
    )
    return SimpleNamespace(org=org, user=user, workflow=workflow, run=run)


async def _set_token(async_session, run_id, token, expires_at):
    """Force a token/expiry onto a run to simulate legacy/expired state."""
    run = await async_session.get(WorkflowRunModel, run_id)
    run.public_access_token = token
    run.public_access_token_expires_at = expires_at
    await async_session.flush()


async def _reload(async_session, run_id):
    run = await async_session.get(WorkflowRunModel, run_id)
    await async_session.refresh(run)
    return run


class TestPublicTokenLifecycle:
    async def test_ensure_generates_token_with_future_expiry(
        self, db_session, async_session, seeded_run
    ):
        token = await db_session.ensure_public_access_token(seeded_run.run.id)

        assert token
        run = await _reload(async_session, seeded_run.run.id)
        assert run.public_access_token == token
        assert run.public_access_token_expires_at > datetime.now(UTC)

    async def test_ensure_reuses_valid_token(
        self, db_session, async_session, seeded_run
    ):
        first = await db_session.ensure_public_access_token(seeded_run.run.id)
        before = await _reload(async_session, seeded_run.run.id)
        first_expiry = before.public_access_token_expires_at

        second = await db_session.ensure_public_access_token(seeded_run.run.id)
        after = await _reload(async_session, seeded_run.run.id)

        assert second == first
        assert after.public_access_token_expires_at == first_expiry

    async def test_ensure_rotates_expired_token(
        self, db_session, async_session, seeded_run
    ):
        first = await db_session.ensure_public_access_token(seeded_run.run.id)
        await _set_token(
            async_session,
            seeded_run.run.id,
            first,
            datetime.now(UTC) - timedelta(days=1),
        )

        second = await db_session.ensure_public_access_token(seeded_run.run.id)

        assert second != first
        run = await _reload(async_session, seeded_run.run.id)
        assert run.public_access_token_expires_at > datetime.now(UTC)

    async def test_ensure_rotates_legacy_null_expiry_token(
        self, db_session, async_session, seeded_run
    ):
        await _set_token(async_session, seeded_run.run.id, "legacy-token", None)

        token = await db_session.ensure_public_access_token(seeded_run.run.id)

        assert token != "legacy-token"
        run = await _reload(async_session, seeded_run.run.id)
        assert run.public_access_token_expires_at is not None

    async def test_lookup_returns_run_for_valid_token(self, db_session, seeded_run):
        token = await db_session.ensure_public_access_token(seeded_run.run.id)

        found = await db_session.get_workflow_run_by_public_token(token)

        assert found is not None
        assert found.id == seeded_run.run.id

    async def test_lookup_rejects_expired_token(
        self, db_session, async_session, seeded_run
    ):
        token = await db_session.ensure_public_access_token(seeded_run.run.id)
        await _set_token(
            async_session,
            seeded_run.run.id,
            token,
            datetime.now(UTC) - timedelta(seconds=1),
        )

        assert await db_session.get_workflow_run_by_public_token(token) is None

    async def test_lookup_rejects_legacy_null_expiry_token(
        self, db_session, async_session, seeded_run
    ):
        await _set_token(async_session, seeded_run.run.id, "legacy-token", None)

        assert await db_session.get_workflow_run_by_public_token("legacy-token") is None

    async def test_revoke_clears_token_and_blocks_lookup(
        self, db_session, async_session, seeded_run
    ):
        token = await db_session.ensure_public_access_token(seeded_run.run.id)

        revoked = await db_session.revoke_public_access_token(
            seeded_run.run.id,
            workflow_id=seeded_run.workflow.id,
            organization_id=seeded_run.org.id,
        )

        assert revoked is True
        assert await db_session.get_workflow_run_by_public_token(token) is None
        run = await _reload(async_session, seeded_run.run.id)
        assert run.public_access_token is None
        assert run.public_access_token_expires_at is None

    async def test_revoke_is_org_scoped(self, db_session, seeded_run):
        token = await db_session.ensure_public_access_token(seeded_run.run.id)

        revoked = await db_session.revoke_public_access_token(
            seeded_run.run.id,
            workflow_id=seeded_run.workflow.id,
            organization_id=seeded_run.org.id + 9999,
        )

        assert revoked is False
        # Token from another org must remain valid.
        assert await db_session.get_workflow_run_by_public_token(token) is not None

    async def test_revoke_is_workflow_scoped(self, db_session, seeded_run):
        """A mismatched workflow_id (same org) must not revoke the run's token."""
        token = await db_session.ensure_public_access_token(seeded_run.run.id)

        # Another workflow in the same org — its id must not authorize revoking
        # a run that belongs to a different workflow.
        other_workflow = await db_session.create_workflow(
            name="Other WF",
            workflow_definition=GRAPH,
            user_id=seeded_run.user.id,
            organization_id=seeded_run.org.id,
        )

        revoked = await db_session.revoke_public_access_token(
            seeded_run.run.id,
            workflow_id=other_workflow.id,
            organization_id=seeded_run.org.id,
        )

        assert revoked is False
        assert await db_session.get_workflow_run_by_public_token(token) is not None


# ---------------------------------------------------------------------------
# Route wiring (no database)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_endpoint_success(monkeypatch):
    from api.routes import workflow as workflow_route

    revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(workflow_route.db_client, "revoke_public_access_token", revoke)
    user = SimpleNamespace(selected_organization_id=42)

    result = await workflow_route.revoke_workflow_run_public_token(7, 99, user=user)

    assert result == {"revoked": True}
    revoke.assert_awaited_once_with(99, workflow_id=7, organization_id=42)


@pytest.mark.asyncio
async def test_revoke_endpoint_404_for_other_org(monkeypatch):
    from api.routes import workflow as workflow_route

    monkeypatch.setattr(
        workflow_route.db_client,
        "revoke_public_access_token",
        AsyncMock(return_value=False),
    )
    user = SimpleNamespace(selected_organization_id=42)

    with pytest.raises(HTTPException) as exc:
        await workflow_route.revoke_workflow_run_public_token(7, 99, user=user)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_rejects_expired_or_missing_token(monkeypatch):
    from api.routes import public_download

    # An expired/legacy token resolves to no run at the freshness-enforcing
    # lookup, so the endpoint must 404 rather than mint a signed URL.
    monkeypatch.setattr(
        public_download.db_client,
        "get_workflow_run_by_public_token",
        AsyncMock(return_value=None),
    )

    with pytest.raises(HTTPException) as exc:
        await public_download.download_workflow_artifact(
            token="expired-token", artifact_type="recording"
        )

    assert exc.value.status_code == 404
