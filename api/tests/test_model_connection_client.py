"""Database checks for catalog ownership, credential rotation and reference guards."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from api.db.model_connection_client import ModelCatalogConflict, ModelCatalogNotFound
from api.routes import model_connections as routes
from api.services.auth.depends import get_user_with_selected_organization


async def create_org(client):
    user, _ = await client.get_or_create_user_by_provider_id(str(uuid4()))
    org, _ = await client.get_or_create_organization_by_provider_id(
        str(uuid4()), user.id
    )
    return org.id


async def create_connection(client, org, key="original"):
    return await client.create_provider_connection(
        org,
        name="Dograh",
        provider="dograh",
        credentials={"api_key": key},
        connection_settings={},
    )


def pipeline(connection):
    return {
        "version": 3,
        "mode": "pipeline",
        **{
            role: {"provider_connection_uuid": connection.uuid, "settings": {}}
            for role in ("llm", "tts", "stt")
        },
    }


@pytest.mark.asyncio
async def test_catalog_isolation_and_multiple_dograh_connections(db_session):
    org = await create_org(db_session)
    other = await create_org(db_session)
    first = await create_connection(db_session, org)
    second = await create_connection(db_session, org, "second")
    assert first.uuid != second.uuid
    assert len(await db_session.list_provider_connections(org)) == 2
    assert await db_session.list_provider_connections(other) == []
    assert await db_session.get_provider_connection(other, first.uuid) is None
    with pytest.raises(ModelCatalogNotFound):
        await db_session.update_provider_connection(
            other, first.uuid, changes={"name": "other"}
        )
    with pytest.raises(ModelCatalogNotFound):
        await db_session.create_named_model_configuration(
            other, name="Cross tenant", configuration=pipeline(first)
        )


@pytest.mark.asyncio
async def test_credential_update_replaces_value_and_rejects_stale_edit(db_session):
    org = await create_org(db_session)
    row = await create_connection(db_session, org)
    updated = await db_session.update_provider_connection(
        org, row.uuid, changes={"credentials": {"api_key": "new"}}, expected_revision=1
    )
    assert updated.credentials == {"api_key": "new"}
    assert updated.revision == 2
    with pytest.raises(ModelCatalogConflict):
        await db_session.update_provider_connection(
            org, row.uuid, changes={"name": "stale"}, expected_revision=1
        )
    again = await db_session.update_provider_connection(
        org, row.uuid, changes={"name": "Renamed"}, expected_revision=2
    )
    assert again.credentials == {"api_key": "new"}


@pytest.mark.asyncio
async def test_default_and_connection_archive_reference_guards(db_session):
    org = await create_org(db_session)
    other = await create_org(db_session)
    connection = await create_connection(db_session, org)
    named = await db_session.create_named_model_configuration(
        org, name="Default", configuration=pipeline(connection)
    )
    with pytest.raises(ModelCatalogNotFound):
        await db_session.set_default_named_model_configuration(other, named.uuid)
    await db_session.set_default_named_model_configuration(org, named.uuid)
    with pytest.raises(ModelCatalogConflict):
        await db_session.archive_named_model_configuration(org, named.uuid)
    with pytest.raises(ModelCatalogConflict):
        await db_session.archive_provider_connection(org, connection.uuid)
    spare = await create_connection(db_session, org, "spare")
    await db_session.archive_provider_connection(org, spare.uuid)
    assert await db_session.get_provider_connection(org, spare.uuid) is None
    retained = await db_session.get_provider_connection(
        org, spare.uuid, active_only=False
    )
    assert retained.credentials == {"api_key": "spare"}


@pytest.mark.asyncio
async def test_archived_catalog_lists_and_restore_lifecycle(db_session, monkeypatch):
    org = await create_org(db_session)
    other = await create_org(db_session)
    connection = await create_connection(db_session, org)
    named = await db_session.create_named_model_configuration(
        org, name="Reusable", configuration=pipeline(connection)
    )
    await create_connection(db_session, other, "other-org-secret")
    await db_session.archive_named_model_configuration(org, named.uuid)
    await db_session.archive_provider_connection(org, connection.uuid)
    original_configuration = named.configuration
    monkeypatch.setattr(routes, "db_client", db_session)
    from api.services.configuration import model_connections as resolver

    monkeypatch.setattr(resolver, "db_client", db_session)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: (
        SimpleNamespace(selected_organization_id=org)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for kind, uuid in (
            ("provider-connections", connection.uuid),
            ("model-configurations", named.uuid),
        ):
            active = await client.get(f"/model-connections/{kind}")
            assert active.status_code == 200
            assert active.json() == []
            archived = await client.get(
                f"/model-connections/{kind}", params={"include_archived": True}
            )
            assert archived.status_code == 200
            assert [row["uuid"] for row in archived.json()] == [uuid]
            assert archived.json()[0]["is_active"] is False
            assert "other-org-secret" not in archived.text
            assert "original" not in archived.text
        model_path = f"/model-connections/model-configurations/{named.uuid}/restore"
        blocked = await client.post(model_path)
        assert blocked.status_code == 404
        assert await db_session.get_named_model_configuration(org, named.uuid) is None

        restored_provider = await client.post(
            f"/model-connections/provider-connections/{connection.uuid}/restore"
        )
        assert restored_provider.status_code == 200
        assert restored_provider.json()["is_active"] is True
        assert "original" not in restored_provider.text
        assert (
            await db_session.get_provider_connection(org, connection.uuid)
        ).credentials == {"api_key": "original"}

        restored_model = await client.post(model_path)
        assert restored_model.status_code == 200
        assert restored_model.json()["uuid"] == named.uuid
        assert restored_model.json()["is_active"] is True
        assert (
            restored_model.json()["configuration"]["llm"]
            == original_configuration["llm"]
        )
        revision = restored_model.json()["revision"]
        repeated = await client.post(model_path)
        assert repeated.status_code == 200
        assert repeated.json()["revision"] == revision
        default = await client.get("/model-connections/default")
        assert default.json()["model_configuration_uuid"] is None


@pytest.mark.asyncio
async def test_restore_is_org_scoped_and_rechecks_provider_references(db_session):
    org = await create_org(db_session)
    other = await create_org(db_session)
    connection = await create_connection(db_session, org)
    named = await db_session.create_named_model_configuration(
        org, name="Archived", configuration=pipeline(connection)
    )
    await db_session.archive_named_model_configuration(org, named.uuid)
    await db_session.archive_provider_connection(org, connection.uuid)
    with pytest.raises(ModelCatalogNotFound):
        await db_session.restore_provider_connection(other, connection.uuid)
    with pytest.raises(ModelCatalogNotFound):
        await db_session.restore_named_model_configuration(other, named.uuid)
    with pytest.raises(ModelCatalogNotFound):
        await db_session.restore_named_model_configuration(org, named.uuid)
    assert await db_session.get_named_model_configuration(org, named.uuid) is None
    await db_session.restore_provider_connection(org, connection.uuid)
    with pytest.raises(ModelCatalogConflict):
        await db_session.restore_named_model_configuration(
            org, named.uuid, expected_revision=1
        )
    restored = await db_session.restore_named_model_configuration(org, named.uuid)
    assert restored.is_active
    with pytest.raises(ModelCatalogConflict):
        await db_session.archive_provider_connection(org, connection.uuid)


@pytest.mark.asyncio
async def test_campaign_archived_version_prevents_configuration_archive(
    db_session, async_session
):
    from api.db.models import CampaignModel, WorkflowDefinitionModel, WorkflowModel

    org = await create_org(db_session)
    user, _ = await db_session.get_or_create_user_by_provider_id(str(uuid4()))
    connection = await create_connection(db_session, org)
    named = await db_session.create_named_model_configuration(
        org, name="Campaign preset", configuration=pipeline(connection)
    )
    workflow = WorkflowModel(
        name="Campaign workflow", organization_id=org, user_id=user.id
    )
    async_session.add(workflow)
    await async_session.flush()
    definition = WorkflowDefinitionModel(
        workflow_id=workflow.id,
        status="archived",
        workflow_json={},
        workflow_configurations={
            "model_configuration_override": {"model_configuration_uuid": named.uuid}
        },
    )
    async_session.add(definition)
    await async_session.flush()
    campaign = CampaignModel(
        name="Pinned campaign",
        organization_id=org,
        workflow_id=workflow.id,
        created_by=user.id,
        source_id="test",
        state="paused",
        orchestrator_metadata={
            "traffic_split": {
                "variants": [
                    {
                        "workflow_id": workflow.id,
                        "workflow_definition_id": definition.id,
                        "weight": 100,
                    }
                ]
            }
        },
    )
    async_session.add(campaign)
    await async_session.flush()
    with pytest.raises(ModelCatalogConflict, match="campaign"):
        await db_session.archive_named_model_configuration(org, named.uuid)


@pytest.mark.asyncio
async def test_index_metadata_lookup_is_scoped_to_organization_and_documents(
    db_session, async_session
):
    from api.db.models import KnowledgeBaseChunkModel, KnowledgeBaseDocumentModel

    org = await create_org(db_session)
    other = await create_org(db_session)
    user, _ = await db_session.get_or_create_user_by_provider_id(str(uuid4()))
    documents = []
    for owner, model in (
        (org, "first-model"),
        (org, "second-model"),
        (other, "private-model"),
    ):
        document = KnowledgeBaseDocumentModel(
            organization_id=owner,
            filename="test.txt",
            created_by=user.id,
            processing_status="completed",
        )
        async_session.add(document)
        await async_session.flush()
        async_session.add(
            KnowledgeBaseChunkModel(
                organization_id=owner,
                document_id=document.id,
                chunk_text="test",
                chunk_index=0,
                embedding_model=model,
                embedding_dimension=1536,
            )
        )
        documents.append(document)
    await async_session.flush()
    assert await db_session.get_model_configuration_embedding_spaces(
        org, document_uuids=[documents[0].document_uuid, documents[2].document_uuid]
    ) == [{"model": "first-model", "dimension": 1536}]
    assert len(await db_session.get_model_configuration_embedding_spaces(org)) == 2


@pytest.mark.asyncio
async def test_fallback_reference_guard_checks_tenant_and_prevents_archive(db_session):
    org = await create_org(db_session)
    other = await create_org(db_session)
    primary = await create_connection(db_session, org)
    backup = await create_connection(db_session, org, "backup")
    foreign = await create_connection(db_session, other)
    config = pipeline(primary)
    config["llm_fallback"] = {
        "version": 1,
        "rules": [
            {
                "condition": {"type": "error"},
                "target": {"provider_connection_uuid": foreign.uuid, "settings": {}},
            }
        ],
    }
    with pytest.raises(ModelCatalogNotFound):
        await db_session.create_named_model_configuration(
            org, name="Foreign fallback", configuration=config
        )
    config["llm_fallback"]["rules"][0]["target"]["provider_connection_uuid"] = (
        backup.uuid
    )
    await db_session.create_named_model_configuration(
        org, name="Fallback", configuration=config
    )
    with pytest.raises(ModelCatalogConflict):
        await db_session.archive_provider_connection(org, backup.uuid)
