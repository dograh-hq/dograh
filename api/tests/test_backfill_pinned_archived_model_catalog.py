"""Archived versions pinned by a live campaign keep their own model settings.

b3c1d9e4f7a2 skips archived definitions. A campaign can still run one of those
versions, and catalog resolution would otherwise inherit the organization
default. The follow-up revision binds just those pins.
"""

import importlib.util
import json
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
from api.tests.test_backfill_model_catalog_v3_migration import (
    BYOK_PIPELINE_V2,
    DOGRAH_V2,
)
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


def _latest_split(workflow_id):
    """A stored split whose only variant tracks the released definition."""
    return {
        "traffic_split": {
            "seed": "latest",
            "revision": 1,
            "variants": [
                {
                    "id": f"{workflow_id}:latest",
                    "workflow_id": workflow_id,
                    "workflow_definition_id": None,
                    "weight": 100,
                }
            ],
        }
    }


def _pin_split(workflow_id, definition_id):
    return {
        "traffic_split": {
            "seed": str(definition_id),
            "revision": 1,
            "variants": [
                {
                    "id": f"{workflow_id}:{definition_id}",
                    "workflow_id": workflow_id,
                    "workflow_definition_id": definition_id,
                    "weight": 100,
                }
            ],
        }
    }


@pytest.fixture
async def gaps(test_engine):
    """Cases the numeric-pin backfill missed, plus controls it must ignore.

    unsplit/latest/legacy: a non-terminal campaign runs an archived
    workflow's released (or, with no released pointer, current) definition.
    null_binding: the override key is present and JSON null.
    partial: a voice-only override, checked after the catalog default's
    credential is rotated away from the V2 row.
    full: a complete inline override, which keeps its own credentials.
    empty_binding, idle, and done stay unbound.
    """
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    previous = db_client.engine, db_client.async_session
    db_client.engine, db_client.async_session = test_engine, factory
    ids = None
    try:
        async with factory() as session:
            organization = OrganizationModel(provider_id=f"pinned-gaps-{uuid4()}")
            session.add(organization)
            await session.flush()
            user = UserModel(
                provider_id=f"pinned-gaps-{uuid4()}",
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

            def workflow(name, status):
                row = WorkflowModel(
                    organization_id=organization.id,
                    user_id=user.id,
                    name=name,
                    status=status,
                    workflow_configurations={},
                )
                session.add(row)
                return row

            def definition(owner, status, configurations, *, is_current=False):
                row = WorkflowDefinitionModel(
                    workflow_id=owner.id,
                    workflow_json={},
                    workflow_configurations=configurations,
                    status=status,
                    is_current=is_current,
                )
                session.add(row)
                return row

            def campaign(name, owner, state, metadata):
                session.add(
                    CampaignModel(
                        name=name,
                        organization_id=organization.id,
                        workflow_id=owner.id,
                        created_by=user.id,
                        source_type="csv",
                        source_id=f"{name}.csv",
                        state=state,
                        orchestrator_metadata=metadata,
                    )
                )

            active = workflow("Active", "active")
            unsplit = workflow("Unsplit", "archived")
            latest = workflow("Latest", "archived")
            legacy = workflow("Legacy", "archived")
            null_binding = workflow("NullBind", "archived")
            empty_binding = workflow("EmptyBind", "archived")
            partial = workflow("Partial", "archived")
            full = workflow("Full", "archived")
            idle = workflow("Idle", "archived")
            done = workflow("Done", "archived")
            await session.flush()

            active_def = definition(
                active, "published", _voice("active-voice"), is_current=True
            )
            released = definition(
                unsplit, "published", _voice("released-voice"), is_current=True
            )
            unused = definition(unsplit, "archived", _voice("unused-voice"))
            latest_def = definition(
                latest, "published", _voice("latest-voice"), is_current=True
            )
            legacy_def = definition(
                legacy, "published", _voice("legacy-voice"), is_current=True
            )
            null_def = definition(
                null_binding,
                "published",
                {
                    "model_configuration_override": None,
                    "model_overrides": {"tts": {"voice": "null-voice"}},
                },
                is_current=True,
            )
            empty_def = definition(
                empty_binding,
                "published",
                {
                    "model_configuration_override": {},
                    "model_overrides": {"tts": {"voice": "empty-voice"}},
                },
                is_current=True,
            )
            partial_def = definition(
                partial, "published", _voice("partial-voice"), is_current=True
            )
            full_def = definition(
                full,
                "published",
                {"model_configuration_v2_override": BYOK_PIPELINE_V2},
                is_current=True,
            )
            idle_def = definition(
                idle, "published", _voice("idle-voice"), is_current=True
            )
            done_def = definition(
                done, "published", _voice("done-voice"), is_current=True
            )
            await session.flush()
            for owner, released_definition in (
                (active, active_def),
                (unsplit, released),
                (latest, latest_def),
                (null_binding, null_def),
                (empty_binding, empty_def),
                (partial, partial_def),
                (full, full_def),
                (idle, idle_def),
                (done, done_def),
            ):
                owner.released_definition_id = released_definition.id

            campaign("active-latest", active, "running", {})
            campaign("no-split", unsplit, "running", {})
            # The campaign row points at the active workflow; the variant
            # tracks Latest's released definition.
            campaign("latest-variant", active, "running", _latest_split(latest.id))
            campaign("legacy-current", legacy, "running", {})
            campaign(
                "null-pin",
                null_binding,
                "running",
                _pin_split(null_binding.id, null_def.id),
            )
            campaign(
                "empty-pin",
                empty_binding,
                "running",
                _pin_split(empty_binding.id, empty_def.id),
            )
            campaign(
                "partial-pin",
                partial,
                "running",
                _pin_split(partial.id, partial_def.id),
            )
            campaign("full-pin", full, "running", _pin_split(full.id, full_def.id))
            campaign("completed", done, "completed", {})
            await session.commit()
            ids = {
                "organization": organization.id,
                "user": user.id,
                "active": active_def.id,
                "released": released.id,
                "unused": unused.id,
                "latest": latest_def.id,
                "legacy": legacy_def.id,
                "null": null_def.id,
                "empty": empty_def.id,
                "partial": partial_def.id,
                "full": full_def.id,
                "idle": idle_def.id,
                "done": done_def.id,
                "unsplit_workflow": unsplit.id,
                "legacy_workflow": legacy.id,
                "latest_workflow": latest.id,
            }
        yield factory, ids
    finally:
        db_client.engine, db_client.async_session = previous
        if ids is not None:
            await _cleanup(factory, ids)


async def _rotate_default(factory, organization_id, api_key="rotated-key"):
    """Point the catalog default at a new Dograh credential. The V2 row stays."""
    async with factory() as session:
        default_uuid = await session.scalar(
            select(OrganizationConfigurationModel.value).where(
                OrganizationConfigurationModel.organization_id == organization_id,
                OrganizationConfigurationModel.key
                == "MODEL_CONFIGURATION_DEFAULT_UUID",
            )
        )
        default = await session.scalar(
            select(NamedModelConfigurationModel).where(
                NamedModelConfigurationModel.uuid == default_uuid
            )
        )
        previous = default.configuration["tts"]["provider_connection_uuid"]
        rotated = ProviderConnectionModel(
            organization_id=organization_id,
            name="Rotated Dograh",
            provider="dograh",
            credentials={"api_key": api_key},
            connection_settings={},
        )
        session.add(rotated)
        await session.flush()
        spec = json.loads(json.dumps(default.configuration))
        for selection in spec.values():
            if isinstance(selection, dict) and "provider_connection_uuid" in selection:
                selection["provider_connection_uuid"] = rotated.uuid
        named = NamedModelConfigurationModel(
            organization_id=organization_id,
            name="Rotated default",
            configuration=spec,
        )
        session.add(named)
        await session.flush()
        await session.execute(
            update(OrganizationConfigurationModel)
            .where(
                OrganizationConfigurationModel.organization_id == organization_id,
                OrganizationConfigurationModel.key
                == "MODEL_CONFIGURATION_DEFAULT_UUID",
            )
            .values(value=named.uuid)
        )
        await session.commit()
        return previous, rotated.uuid


async def _credentials(factory, organization_id):
    async with factory() as session:
        return {
            row.uuid: row.credentials
            for row in await session.scalars(
                select(ProviderConnectionModel).where(
                    ProviderConnectionModel.organization_id == organization_id
                )
            )
        }


@pytest.mark.asyncio
async def test_follow_up_binds_released_latest_null_and_current_credential(
    test_engine, gaps
):
    factory, ids = gaps
    organization_id = ids["organization"]

    unsplit = await db_client.get_workflow(
        ids["unsplit_workflow"], organization_id=organization_id
    )
    legacy = await db_client.get_workflow(
        ids["legacy_workflow"], organization_id=organization_id
    )
    latest = await db_client.get_workflow(
        ids["latest_workflow"], organization_id=organization_id
    )
    # No pin and a latest variant both resolve to the released definition.
    # A workflow with no released pointer falls back to its current one.
    assert (await definition_to_run(db_client, unsplit)).id == ids["released"]
    assert (await definition_to_run(db_client, latest)).id == ids["latest"]
    assert (await definition_to_run(db_client, legacy)).id == ids["legacy"]

    await _migrate(test_engine, original.migrate)
    _, definitions, _, _ = await _snapshot(factory, organization_id)
    assert definitions[ids["null"]]["model_configuration_override"] is None
    assert definitions[ids["empty"]]["model_configuration_override"] == {}
    active_binding = definitions[ids["active"]]["model_configuration_override"]
    for definition_id in (
        "released",
        "latest",
        "legacy",
        "null",
        "partial",
        "full",
        "idle",
        "done",
        "unused",
    ):
        assert "model_configuration_uuid" not in (
            definitions[ids[definition_id]].get("model_configuration_override") or {}
        )

    previous_connection, rotated_connection = await _rotate_default(
        factory, organization_id
    )
    # Still unbound, so resolution uses the rotated default and drops the voice.
    partial_before = await get_effective_ai_model_configuration_for_workflow(
        organization_id=organization_id,
        workflow_configurations=definitions[ids["partial"]],
    )
    assert partial_before.tts.voice == "default"
    assert partial_before.tts.api_key == "rotated-key"

    summary = await _migrate(test_engine, follow_up.migrate)
    assert summary["inherited"] == []
    assert summary["bindings"] == 6

    configurations, definitions, _, default_uuid = await _snapshot(
        factory, organization_id
    )
    credentials = await _credentials(factory, organization_id)
    default_connection = configurations[default_uuid].configuration["tts"][
        "provider_connection_uuid"
    ]
    assert default_connection == rotated_connection
    assert credentials[previous_connection]["api_key"] == "svc-key"
    assert credentials[rotated_connection]["api_key"] == "rotated-key"

    for definition_id, voice in (
        ("released", "released-voice"),
        ("latest", "latest-voice"),
        ("legacy", "legacy-voice"),
        ("null", "null-voice"),
        ("partial", "partial-voice"),
    ):
        assert _bound_voice(configurations, definitions, ids[definition_id]) == voice
        binding = definitions[ids[definition_id]]["model_configuration_override"]
        spec = configurations[binding["model_configuration_uuid"]].configuration
        assert spec["tts"]["provider_connection_uuid"] == rotated_connection
        resolved = await get_effective_ai_model_configuration_for_workflow(
            organization_id=organization_id,
            workflow_configurations=definitions[ids[definition_id]],
        )
        assert resolved.tts.voice == voice
        assert resolved.tts.api_key == "rotated-key"

    full_binding = definitions[ids["full"]]["model_configuration_override"]
    full_spec = configurations[full_binding["model_configuration_uuid"]].configuration
    full_connection = full_spec["tts"]["provider_connection_uuid"]
    assert full_connection not in {previous_connection, rotated_connection}
    assert credentials[full_connection]["api_key"] == ["dg-key"]
    assert full_spec["tts"]["settings"]["voice"] == "aura-2-thalia-en"

    # Already bound, explicitly empty, or not referenced by a live campaign.
    assert definitions[ids["active"]]["model_configuration_override"] == active_binding
    assert definitions[ids["empty"]]["model_configuration_override"] == {}
    for definition_id in ("unused", "idle", "done"):
        assert "model_configuration_override" not in definitions[ids[definition_id]]
    assert (
        await _resolved_voice(organization_id, definitions[ids["empty"]]) == "default"
    )
    assert await _resolved_voice(organization_id, definitions[ids["idle"]]) == "default"
    assert await _resolved_voice(organization_id, definitions[ids["done"]]) == "default"

    again = await _migrate(test_engine, follow_up.migrate)
    assert again["organizations"] == 0
    assert again["bindings"] == 0
    assert again["connections"] == 0
    _, _, connections_after, _ = await _snapshot(factory, organization_id)
    credentials_after = await _credentials(factory, organization_id)
    assert len(credentials_after) == connections_after
