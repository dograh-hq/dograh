"""Archived versions pinned by a live campaign keep their own model settings.

b3c1d9e4f7a2 skips archived definitions. A campaign can still run one of those
versions, and catalog resolution would otherwise inherit the organization
default. The follow-up revision binds just those pins.
"""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db import db_client
from api.db.models import (
    CampaignModel,
    NamedModelConfigurationModel,
    OrganizationConfigurationModel,
    OrganizationModel,
    ProviderConnectionModel,
    UserModel,
    WorkflowDefinitionModel,
    WorkflowModel,
)
from api.services.configuration.ai_model_configuration import (
    get_effective_ai_model_configuration_for_workflow,
)
from api.services.workflow.run_creation import definition_to_run
from api.tests.test_backfill_model_catalog_v3_migration import DOGRAH_V2
from api.tests.test_backfill_model_catalog_v3_migration import migration as original

_FOLLOW_UP_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "e7a4c91d2b58_backfill_pinned_archived_model_catalog.py"
)
_spec = importlib.util.spec_from_file_location(
    "backfill_pinned_archived_model_catalog", _FOLLOW_UP_PATH
)
follow_up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(follow_up)


def _voice(voice):
    return {"model_overrides": {"tts": {"voice": voice}}}


async def _migrate(test_engine, fn):
    async with test_engine.begin() as connection:
        return await connection.run_sync(fn)


async def _cleanup(factory, ids):
    organization_id = ids["organization"]
    async with factory() as session:
        await session.execute(
            CampaignModel.__table__.delete().where(
                CampaignModel.organization_id == organization_id
            )
        )
        workflows = select(WorkflowModel.id).where(
            WorkflowModel.organization_id == organization_id
        )
        await session.execute(
            update(WorkflowModel)
            .where(WorkflowModel.organization_id == organization_id)
            .values(released_definition_id=None)
        )
        await session.execute(
            WorkflowDefinitionModel.__table__.delete().where(
                WorkflowDefinitionModel.workflow_id.in_(workflows)
            )
        )
        for model in (
            WorkflowModel,
            NamedModelConfigurationModel,
            ProviderConnectionModel,
            OrganizationConfigurationModel,
        ):
            await session.execute(
                model.__table__.delete().where(model.organization_id == organization_id)
            )
        await session.execute(
            UserModel.__table__.delete().where(UserModel.id == ids["user"])
        )
        await session.execute(
            OrganizationModel.__table__.delete().where(
                OrganizationModel.id == organization_id
            )
        )
        await session.commit()


@pytest.fixture
async def pinned(test_engine):
    """Greptile's campaign pin: archived historic voice, still being dialed.

    Also a paused pin (still non-terminal), an archived version nobody is
    running, and a completed campaign on an archived workflow. Only the first
    two may gain a binding.
    """
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    previous = db_client.engine, db_client.async_session
    db_client.engine, db_client.async_session = test_engine, factory
    ids = None
    try:
        async with factory() as session:
            organization = OrganizationModel(provider_id=f"pinned-catalog-{uuid4()}")
            session.add(organization)
            await session.flush()
            user = UserModel(
                provider_id=f"pinned-catalog-{uuid4()}",
                selected_organization_id=organization.id,
            )
            session.add(user)
            await session.flush()
            session.add(
                OrganizationConfigurationModel(
                    organization_id=organization.id,
                    key="MODEL_CONFIGURATION_V2",
                    value=DOGRAH_V2,
                )
            )
            sales = WorkflowModel(
                organization_id=organization.id,
                user_id=user.id,
                name="Sales",
                status="active",
                workflow_configurations={},
            )
            archived = WorkflowModel(
                organization_id=organization.id,
                user_id=user.id,
                name="Old",
                status="archived",
                workflow_configurations=_voice("retired-voice"),
            )
            session.add_all([sales, archived])
            await session.flush()

            def definition(workflow, status, voice, *, is_current=False):
                row = WorkflowDefinitionModel(
                    workflow_id=workflow.id,
                    workflow_json={},
                    workflow_configurations=_voice(voice),
                    status=status,
                    is_current=is_current,
                )
                session.add(row)
                return row

            current = definition(sales, "published", "current-voice", is_current=True)
            historic = definition(sales, "archived", "historic-voice")
            paused = definition(sales, "archived", "paused-voice")
            unused = definition(sales, "archived", "unused-voice")
            retired = definition(
                archived, "published", "retired-voice", is_current=True
            )
            await session.flush()
            sales.released_definition_id = current.id
            archived.released_definition_id = retired.id

            def campaign(name, workflow, state, definition_id):
                session.add(
                    CampaignModel(
                        name=name,
                        organization_id=organization.id,
                        workflow_id=workflow.id,
                        created_by=user.id,
                        source_type="csv",
                        source_id=f"{name}.csv",
                        state=state,
                        orchestrator_metadata={
                            "traffic_split": {
                                "seed": name,
                                "revision": 1,
                                "variants": [
                                    {
                                        "id": f"{workflow.id}:{definition_id}",
                                        "workflow_id": workflow.id,
                                        "workflow_definition_id": definition_id,
                                        "weight": 100,
                                    }
                                ],
                            }
                        },
                    )
                )

            campaign("running-pin", sales, "running", historic.id)
            campaign("paused-pin", sales, "paused", paused.id)
            campaign("completed-pin", archived, "completed", retired.id)
            await session.commit()
            ids = {
                "organization": organization.id,
                "user": user.id,
                "sales": sales.id,
                "archived_workflow": archived.id,
                "current": current.id,
                "historic": historic.id,
                "paused": paused.id,
                "unused": unused.id,
                "retired": retired.id,
            }
        yield factory, ids
    finally:
        db_client.engine, db_client.async_session = previous
        if ids is not None:
            await _cleanup(factory, ids)


async def _snapshot(factory, organization_id):
    async with factory() as session:
        configurations = {
            row.uuid: row
            for row in await session.scalars(
                select(NamedModelConfigurationModel).where(
                    NamedModelConfigurationModel.organization_id == organization_id
                )
            )
        }
        connection_count = await session.scalar(
            select(func.count())
            .select_from(ProviderConnectionModel)
            .where(ProviderConnectionModel.organization_id == organization_id)
        )
        definitions = {
            row.id: row.workflow_configurations
            for row in await session.scalars(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id.in_(
                        select(WorkflowModel.id).where(
                            WorkflowModel.organization_id == organization_id
                        )
                    )
                )
            )
        }
        default_uuid = await session.scalar(
            select(OrganizationConfigurationModel.value).where(
                OrganizationConfigurationModel.organization_id == organization_id,
                OrganizationConfigurationModel.key
                == "MODEL_CONFIGURATION_DEFAULT_UUID",
            )
        )
    return configurations, definitions, connection_count, default_uuid


def _bound_voice(configurations, definitions, definition_id):
    binding = (definitions[definition_id] or {}).get("model_configuration_override")
    if not binding:
        return None
    spec = configurations[binding["model_configuration_uuid"]].configuration
    return spec["tts"]["settings"]["voice"]


async def _resolved_voice(organization_id, configurations):
    effective = await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id,
        workflow_configurations=configurations,
    )
    return effective.tts.voice


@pytest.mark.asyncio
async def test_pinned_archived_version_keeps_its_voice_after_backfill(
    test_engine, pinned
):
    factory, ids = pinned
    organization_id = ids["organization"]

    workflow = await db_client.get_workflow(
        ids["sales"], organization_id=organization_id
    )
    selected = await definition_to_run(
        db_client, workflow, definition_id=ids["historic"]
    )
    assert (selected.id, selected.status) == (ids["historic"], "archived")

    await _migrate(test_engine, original.migrate)
    configurations, definitions, connections, default_uuid = await _snapshot(
        factory, organization_id
    )
    # The shipped backfill binds the live version and leaves the pin unbound,
    # so resolution of that version is the organization default.
    assert _bound_voice(configurations, definitions, ids["current"]) == "current-voice"
    assert "model_configuration_override" not in definitions[ids["historic"]]
    assert "model_configuration_override" not in definitions[ids["paused"]]
    assert "model_configuration_override" not in definitions[ids["unused"]]
    assert "model_configuration_override" not in definitions[ids["retired"]]
    assert (
        await _resolved_voice(organization_id, definitions[ids["historic"]])
        == "default"
    )
    assert connections == 1

    summary = await _migrate(test_engine, follow_up.migrate)
    assert summary["inherited"] == []
    assert summary["bindings"] == 2
    assert summary["connections"] == 0

    configurations, definitions, connections, default_uuid = await _snapshot(
        factory, organization_id
    )
    assert (
        _bound_voice(configurations, definitions, ids["historic"]) == "historic-voice"
    )
    assert _bound_voice(configurations, definitions, ids["paused"]) == "paused-voice"
    assert "model_configuration_override" not in definitions[ids["unused"]]
    assert "model_configuration_override" not in definitions[ids["retired"]]
    # Same Dograh account as the organization default; only the voice differs.
    historic_binding = definitions[ids["historic"]]["model_configuration_override"]
    historic_spec = configurations[
        historic_binding["model_configuration_uuid"]
    ].configuration
    default_spec = configurations[default_uuid].configuration
    assert (
        historic_spec["tts"]["provider_connection_uuid"]
        == default_spec["tts"]["provider_connection_uuid"]
    )
    assert connections == 1
    assert (
        await _resolved_voice(organization_id, definitions[ids["historic"]])
        == "historic-voice"
    )
    assert (
        await _resolved_voice(organization_id, definitions[ids["paused"]])
        == "paused-voice"
    )
    assert (
        await _resolved_voice(organization_id, definitions[ids["retired"]]) == "default"
    )
    assert (
        await _resolved_voice(organization_id, definitions[ids["unused"]]) == "default"
    )

    again = await _migrate(test_engine, follow_up.migrate)
    assert again["organizations"] == 0
    assert again["bindings"] == 0
    _, _, connections_after, _ = await _snapshot(factory, organization_id)
    assert connections_after == 1
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NamedModelConfigurationModel)
                .where(NamedModelConfigurationModel.organization_id == organization_id)
            )
            == 4
        )
