from uuid import uuid4

import pytest

from api.db.models import OrganizationModel, UserModel


@pytest.mark.asyncio
async def test_builder_creates_one_unpublished_draft_and_preserves_later_edits(
    db_session, async_session
):
    org = OrganizationModel(provider_id=f"builder-org-{uuid4()}")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id=f"builder-user-{uuid4()}", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()
    arguments = dict(
        workflow_uuid=str(uuid4()),
        name="Builder draft",
        workflow_definition={"nodes": [], "edges": []},
        user_id=user.id,
        organization_id=org.id,
    )
    first = await db_session.create_builder_draft(**arguments)
    second = await db_session.create_builder_draft(**arguments)
    assert first.id == second.id
    assert first.released_definition_id is None
    draft = await db_session.get_draft_version(first.id)
    assert draft.status == "draft"
    assert draft.version_number == 1
    assert not draft.is_current
    assert draft.published_at is None
    with pytest.raises(ValueError, match="already saved"):
        await db_session.create_builder_draft(
            **{**arguments, "workflow_definition": {"nodes": ["different"]}}
        )
