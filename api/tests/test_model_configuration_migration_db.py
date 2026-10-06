"""Real transaction checks for normalization; uses api/.env.test only."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.db_client import DBClient
from api.db.models import (
    NamedModelConfigurationModel,
    OrganizationConfigurationModel,
    OrganizationModel,
    ProviderConnectionModel,
    WorkflowDefinitionModel,
    WorkflowModel,
)
from api.schemas.model_configuration_migration import MODEL_CONFIGURATION_DEFAULT_KEY
from api.services.configuration.model_configuration_migration import (
    build_model_configuration_migration_plan,
)


@pytest.fixture
async def migration_db(test_engine):
    client = DBClient.__new__(DBClient)
    client.async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with client.async_session() as session:
        organization = OrganizationModel(
            provider_id=f"catalog-migration-test-{uuid4()}"
        )
        session.add(organization)
        await session.flush()
        organization_id = organization.id
        session.add(
            OrganizationConfigurationModel(
                organization_id=organization_id,
                key="MODEL_CONFIGURATION_V2",
                value={
                    "version": 2,
                    "mode": "dograh",
                    "dograh": {"api_key": "test-migration-key"},
                },
            )
        )
        await session.commit()
    try:
        yield client, organization_id
    finally:
        async with client.async_session() as session:
            workflows = select(WorkflowModel.id).where(
                WorkflowModel.organization_id == organization_id
            )
            await session.execute(
                update(WorkflowModel)
                .where(WorkflowModel.organization_id == organization_id)
                .values(released_definition_id=None)
            )
            await session.execute(
                delete(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id.in_(workflows)
                )
            )
            await session.execute(
                delete(WorkflowModel).where(
                    WorkflowModel.organization_id == organization_id
                )
            )
            await session.execute(
                delete(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_dry_run_is_read_only_apply_is_idempotent_and_edits_are_retained(
    migration_db,
):
    client, organization_id = migration_db
    preview = await client.run_model_configuration_migration_transaction(
        organization_id, build_model_configuration_migration_plan
    )
    assert preview.status == "planned"
    assert len(preview.connections) == 1
    async with client.async_session() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderConnectionModel)
                .where(ProviderConnectionModel.organization_id == organization_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(OrganizationConfigurationModel.value).where(
                    OrganizationConfigurationModel.organization_id == organization_id,
                    OrganizationConfigurationModel.key
                    == MODEL_CONFIGURATION_DEFAULT_KEY,
                )
            )
            is None
        )
    applied = await client.run_model_configuration_migration_transaction(
        organization_id, build_model_configuration_migration_plan, apply=True
    )
    assert applied.status == "applied"
    async with client.async_session() as session:
        await session.execute(
            update(NamedModelConfigurationModel)
            .where(NamedModelConfigurationModel.organization_id == organization_id)
            .values(name="Edited after migration", revision=2)
        )
        await session.commit()
    repeated = await client.run_model_configuration_migration_transaction(
        organization_id, build_model_configuration_migration_plan, apply=True
    )
    assert repeated.status == "already_migrated"
    async with client.async_session() as session:
        assert (
            await session.scalar(
                select(NamedModelConfigurationModel.name).where(
                    NamedModelConfigurationModel.organization_id == organization_id
                )
            )
            == "Edited after migration"
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderConnectionModel)
                .where(ProviderConnectionModel.organization_id == organization_id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_failed_organization_apply_rolls_back_catalog_and_default(migration_db):
    client, organization_id = migration_db

    def invalid_plan(source):
        plan = build_model_configuration_migration_plan(source)
        plan.workflow_updates.append((-1, {}))
        return plan

    with pytest.raises(ValueError, match="migration_workflow_changed_concurrently"):
        await client.run_model_configuration_migration_transaction(
            organization_id, invalid_plan, apply=True
        )
    async with client.async_session() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderConnectionModel)
                .where(ProviderConnectionModel.organization_id == organization_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NamedModelConfigurationModel)
                .where(NamedModelConfigurationModel.organization_id == organization_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(OrganizationConfigurationModel.value).where(
                    OrganizationConfigurationModel.organization_id == organization_id,
                    OrganizationConfigurationModel.key
                    == MODEL_CONFIGURATION_DEFAULT_KEY,
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_concurrent_bootstrap_imports_one_default_and_one_connection(
    migration_db,
):
    client, organization_id = migration_db
    plans = await asyncio.gather(
        *[
            client.run_model_configuration_migration_transaction(
                organization_id,
                lambda source: build_model_configuration_migration_plan(
                    source, include_workflows=False
                ),
                apply=True,
                include_workflows=False,
            )
            for _ in range(2)
        ]
    )
    assert sorted(plan.status for plan in plans) == ["already_configured", "applied"]
    async with client.async_session() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderConnectionModel)
                .where(ProviderConnectionModel.organization_id == organization_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NamedModelConfigurationModel)
                .where(NamedModelConfigurationModel.organization_id == organization_id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_apply_changes_binding_and_retains_original_model_payloads(
    migration_db, monkeypatch
):
    from api.services.configuration import model_connections
    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_workflow,
    )

    client, organization_id = migration_db
    monkeypatch.setattr(model_connections, "db_client", client)
    async with client.async_session() as session:
        workflow = WorkflowModel(
            organization_id=organization_id,
            name="Migration fixture",
            workflow_configurations={
                "model_overrides": {"llm": {"temperature": 0.8}},
                "recording": True,
            },
        )
        session.add(workflow)
        await session.flush()
        published = WorkflowDefinitionModel(
            workflow_id=workflow.id,
            status="published",
            workflow_json={"published": "graph"},
            workflow_configurations={"model_overrides": {"llm": {"temperature": 0.4}}},
        )
        draft = WorkflowDefinitionModel(
            workflow_id=workflow.id,
            status="draft",
            workflow_json={"private": "draft graph"},
            workflow_configurations={
                "model_overrides": {"llm": {"temperature": 0.9}},
                "recording": False,
            },
        )
        session.add_all([published, draft])
        await session.flush()
        workflow.released_definition_id = published.id
        draft_id, workflow_id = draft.id, workflow.id
        await session.commit()
    applied = await client.run_model_configuration_migration_transaction(
        organization_id, build_model_configuration_migration_plan, apply=True
    )
    assert applied.status == "applied"
    async with client.async_session() as session:
        draft = await session.get(WorkflowDefinitionModel, draft_id)
        workflow = await session.get(WorkflowModel, workflow_id)
        assert draft.workflow_json == {"private": "draft graph"}
        assert draft.workflow_configurations["recording"] is False
        assert (
            draft.workflow_configurations["model_overrides"]["llm"]["temperature"]
            == 0.9
        )
        assert workflow.workflow_configurations["recording"] is True
        assert workflow.workflow_configurations["model_overrides"] == {
            "llm": {"temperature": 0.8}
        }
        assert (
            workflow.workflow_configurations["model_configuration_override"]
            == draft.workflow_configurations["model_configuration_override"]
        )
        migrated_settings = draft.workflow_configurations
    effective = await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id, workflow_configurations=migrated_settings
    )
    assert effective.llm.temperature == 0.4

    # The UI omits audit data when saving. Clearing the active override must
    # retain it and explicitly inherit the current org catalog default.
    saved = await client.save_workflow_draft(
        workflow_id, workflow_configurations={"recording": False}
    )
    assert saved.workflow_configurations == {
        "recording": False,
        "model_overrides": {"llm": {"temperature": 0.9}},
        "model_configuration_override": {},
    }
    inherited = await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id,
        workflow_configurations=saved.workflow_configurations,
    )
    assert inherited.llm.temperature is None
    await client.publish_workflow_draft(workflow_id)
    async with client.async_session() as session:
        workflow = await session.get(WorkflowModel, workflow_id)
        assert workflow.workflow_configurations["model_overrides"] == {
            "llm": {"temperature": 0.8}
        }
        assert workflow.workflow_configurations["model_configuration_override"] == {}
        original_org = await session.scalar(
            select(OrganizationConfigurationModel.value).where(
                OrganizationConfigurationModel.organization_id == organization_id,
                OrganizationConfigurationModel.key == "MODEL_CONFIGURATION_V2",
            )
        )
        assert original_org == {
            "version": 2,
            "mode": "dograh",
            "dograh": {"api_key": "test-migration-key"},
        }

    # A fresh draft copied from the release also keeps audit values retired.
    fresh = await client.save_workflow_draft(
        workflow_id, workflow_configurations={"recording": True}
    )
    assert fresh.workflow_configurations["model_configuration_override"] == {}
    assert fresh.workflow_configurations["model_overrides"] == {
        "llm": {"temperature": 0.9}
    }


@pytest.mark.asyncio
async def test_workflow_save_and_connection_archive_cannot_leave_dangling_reference(
    migration_db,
):
    from api.db.model_connection_client import (
        ModelCatalogConflict,
        ModelCatalogNotFound,
    )

    client, organization_id = migration_db
    async with client.async_session() as session:
        workflow = WorkflowModel(
            organization_id=organization_id, name="Reference race fixture"
        )
        session.add(workflow)
        await session.commit()
        workflow_id = workflow.id
    connection = await client.create_provider_connection(
        organization_id,
        name="Race fixture",
        provider="openai",
        credentials={"api_key": "test-key"},
        connection_settings={},
    )
    results = await asyncio.gather(
        client.save_workflow_draft(
            workflow_id,
            workflow_configurations={
                "model_configuration_override": {
                    "llm": {"provider_connection_uuid": connection.uuid}
                }
            },
        ),
        client.archive_provider_connection(organization_id, connection.uuid),
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(errors) == 1
    assert isinstance(errors[0], (ModelCatalogConflict, ModelCatalogNotFound))
    async with client.async_session() as session:
        active = await session.scalar(
            select(ProviderConnectionModel.is_active).where(
                ProviderConnectionModel.uuid == connection.uuid
            )
        )
        draft = await session.scalar(
            select(WorkflowDefinitionModel).where(
                WorkflowDefinitionModel.workflow_id == workflow_id,
                WorkflowDefinitionModel.status == "draft",
            )
        )
        assert active or draft is None


@pytest.mark.asyncio
async def test_workflow_save_checks_configuration_reference_organization(migration_db):
    from api.db.model_connection_client import ModelCatalogNotFound

    client, organization_id = migration_db
    async with client.async_session() as session:
        foreign_org = OrganizationModel(provider_id=f"foreign-catalog-test-{uuid4()}")
        workflow = WorkflowModel(organization_id=organization_id, name="Tenant fixture")
        session.add_all([foreign_org, workflow])
        await session.flush()
        foreign = NamedModelConfigurationModel(
            organization_id=foreign_org.id,
            name="Foreign",
            configuration={"version": 3, "mode": "pipeline"},
        )
        session.add(foreign)
        await session.commit()
        foreign_org_id, foreign_uuid, workflow_id = (
            foreign_org.id,
            foreign.uuid,
            workflow.id,
        )
    try:
        with pytest.raises(ModelCatalogNotFound, match="Model configuration not found"):
            await client.save_workflow_draft(
                workflow_id,
                workflow_configurations={
                    "model_configuration_override": {
                        "model_configuration_uuid": foreign_uuid
                    }
                },
            )
        async with client.async_session() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(WorkflowDefinitionModel)
                    .where(WorkflowDefinitionModel.workflow_id == workflow_id)
                )
                == 0
            )
    finally:
        async with client.async_session() as session:
            await session.execute(
                delete(OrganizationModel).where(OrganizationModel.id == foreign_org_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_publish_rechecks_archived_references(migration_db):
    from api.db.model_connection_client import ModelCatalogNotFound

    client, organization_id = migration_db
    async with client.async_session() as session:
        workflow = WorkflowModel(
            organization_id=organization_id, name="Publish fixture"
        )
        connection = ProviderConnectionModel(
            organization_id=organization_id,
            name="Archived",
            provider="openai",
            credentials={"api_key": "test-key"},
            connection_settings={},
            is_active=False,
        )
        session.add_all([workflow, connection])
        await session.flush()
        draft = WorkflowDefinitionModel(
            workflow_id=workflow.id,
            status="draft",
            workflow_configurations={
                "model_configuration_override": {
                    "llm": {"provider_connection_uuid": connection.uuid}
                }
            },
        )
        session.add(draft)
        await session.commit()
        workflow_id, draft_id = workflow.id, draft.id
    with pytest.raises(ModelCatalogNotFound, match="Provider connection not found"):
        await client.publish_workflow_draft(workflow_id)
    async with client.async_session() as session:
        assert (
            await session.scalar(
                select(WorkflowDefinitionModel.status).where(
                    WorkflowDefinitionModel.id == draft_id
                )
            )
            == "draft"
        )
        assert (
            await session.scalar(
                select(WorkflowModel.released_definition_id).where(
                    WorkflowModel.id == workflow_id
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_workflow_route_saves_typed_named_uuid_as_json_string(
    migration_db, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from api.routes import workflow as routes
    from api.services.configuration import model_connections

    client, organization_id = migration_db
    async with client.async_session() as session:
        workflow = WorkflowModel(
            organization_id=organization_id, name="Route JSON fixture"
        )
        session.add(workflow)
        await session.commit()
        workflow_id = workflow.id
    connection = await client.create_provider_connection(
        organization_id,
        name="Route JSON fixture",
        provider="dograh",
        credentials={"api_key": "test-key"},
        connection_settings={},
    )
    specification = {
        "version": 3,
        "mode": "pipeline",
        **{
            role: {"provider_connection_uuid": connection.uuid, "settings": {}}
            for role in ("llm", "stt", "tts")
        },
    }
    configuration = await client.create_named_model_configuration(
        organization_id, name="Route JSON fixture", configuration=specification
    )
    monkeypatch.setattr(routes, "db_client", client)
    monkeypatch.setattr(model_connections, "db_client", client)
    monkeypatch.setattr(
        routes,
        "apply_external_pbx_mapping_policy",
        AsyncMock(side_effect=lambda incoming, **kwargs: incoming),
    )
    monkeypatch.setattr(
        routes, "validate_workflow_tool_name_collisions", AsyncMock(return_value=[])
    )
    provider_validator = Mock()
    monkeypatch.setattr(routes, "UserConfigurationValidator", provider_validator)
    request = routes.UpdateWorkflowRequest.model_validate(
        {
            "workflow_configurations": {
                "model_configuration_override": {
                    "model_configuration_uuid": configuration.uuid
                }
            }
        }
    )
    assert not isinstance(
        request.workflow_configurations.model_configuration_override.model_configuration_uuid,
        str,
    )
    result = await routes.update_workflow(
        workflow_id,
        request,
        user=SimpleNamespace(
            id=1, provider_id="test-user", selected_organization_id=organization_id
        ),
    )
    assert (
        result["workflow_configurations"]["model_configuration_override"][
            "model_configuration_uuid"
        ]
        == configuration.uuid
    )
    draft = await client.get_draft_version(workflow_id)
    assert (
        draft.workflow_configurations["model_configuration_override"][
            "model_configuration_uuid"
        ]
        == configuration.uuid
    )
    provider_validator.assert_not_called()
