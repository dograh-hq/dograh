import uuid

import pytest

from api.db.models import OrganizationModel, UserModel


@pytest.mark.asyncio
async def test_reserve_embed_token_usage_enforces_limit_and_org_scope(
    db_session, async_session
):
    suffix = uuid.uuid4().hex
    organization = OrganizationModel(provider_id=f"embed-usage-org-{suffix}")
    async_session.add(organization)
    await async_session.flush()

    user = UserModel(
        provider_id=f"embed-usage-user-{suffix}",
        selected_organization_id=organization.id,
    )
    async_session.add(user)
    await async_session.flush()

    workflow = await db_session.create_workflow(
        name="Embed usage reservation test",
        workflow_definition={},
        user_id=user.id,
        organization_id=organization.id,
    )
    embed_token = await db_session.create_embed_token(
        workflow_id=workflow.id,
        organization_id=organization.id,
        created_by=user.id,
        usage_limit=1,
    )

    assert not await db_session.reserve_embed_token_usage(
        embed_token.id, organization.id + 1
    )
    assert await db_session.reserve_embed_token_usage(embed_token.id, organization.id)
    assert not await db_session.reserve_embed_token_usage(
        embed_token.id, organization.id
    )

    await async_session.refresh(embed_token)
    assert embed_token.usage_count == 1
