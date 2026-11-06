from uuid import uuid4

import pytest

from api.db.models import OrganizationModel, UserModel


@pytest.mark.asyncio
async def test_builder_creates_one_published_v1_and_rejects_a_different_retry(
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
    first = await db_session.create_builder_workflow(**arguments)
    second = await db_session.create_builder_workflow(**arguments)
    assert first.id == second.id
    # V1 must be released so entry points that run the published version
    # (embed, inbound numbers, API triggers) can bind a run to it.
    assert first.released_definition_id is not None
    assert await db_session.get_draft_version(first.id) is None
    released = await db_session.get_workflow_definition(
        first.id, first.released_definition_id, org.id
    )
    assert released.status == "published"
    assert released.version_number == 1
    assert released.is_current
    assert released.published_at is not None
    assert released.workflow_json == arguments["workflow_definition"]
    with pytest.raises(ValueError, match="already created"):
        await db_session.create_builder_workflow(
            **{**arguments, "workflow_definition": {"nodes": ["different"]}}
        )
