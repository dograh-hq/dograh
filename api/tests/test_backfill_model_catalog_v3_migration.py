"""Tests for the b3c1d9e4f7a2 catalog backfill's frozen conversion.

The migration writes catalog rows from raw V2 JSON without importing the
application, so these tests check its output against the live schemas and
provider registry: they fail if either drifts away from what it produces.
"""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.models import (
    NamedModelConfigurationModel,
    OrganizationConfigurationModel,
    OrganizationModel,
    ProviderConnectionModel,
    WorkflowDefinitionModel,
    WorkflowModel,
)
from api.schemas.model_connections import (
    CONNECTION_FIELDS,
    CREDENTIAL_FIELDS,
    ModelConfigurationSpec,
)
from api.services.configuration.registry import REGISTRY, ServiceType

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "b3c1d9e4f7a2_backfill_model_catalog_v3.py"
)
_spec = importlib.util.spec_from_file_location(
    "backfill_model_catalog_v3", _MIGRATION_PATH
)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

DOGRAH_V2 = {
    "version": 2,
    "mode": "dograh",
    "dograh": {
        "api_key": "svc-key",
        "voice": "default",
        "speed": 1.0,
        "language": "multi",
    },
}
BYOK_PIPELINE_V2 = {
    "version": 2,
    "mode": "byok",
    "byok": {
        "mode": "pipeline",
        "pipeline": {
            "llm": {
                "provider": "google_vertex",
                "credentials": "{service-account}",
                "project_id": "proj",
                "location": "europe-west1",
                "model": "gemini-3.6-flash",
                "temperature": None,
            },
            "stt": {
                "provider": "deepgram",
                "api_key": ["dg-key"],
                "model": "nova-3-general",
                "language": "it",
            },
            "tts": {
                "provider": "deepgram",
                "api_key": ["dg-key"],
                "voice": "aura-2-thalia-en",
                "model": "aura-2-thalia-en",
            },
        },
    },
}
BYOK_REALTIME_V2 = {
    "version": 2,
    "mode": "byok",
    "byok": {
        "mode": "realtime",
        "realtime": {
            "realtime": {
                "provider": "google_realtime",
                "api_key": ["g-key"],
                "model": "gemini-live",
                "voice": "Puck",
                "temperature": 0.7,
            },
            "llm": {
                "provider": "openai",
                "api_key": ["oa-key"],
                "model": "gpt-4.1-mini",
            },
        },
    },
}


def _rows(catalog):
    connections = {row["uuid"]: row for row in catalog.connections}
    configurations = [
        (row["name"], json.loads(row["configuration"]))
        for row in catalog.configurations
    ]
    return connections, configurations


def _assert_valid_for_registry(spec, connections):
    """The spec parses, and every settings key is one the provider accepts."""
    ModelConfigurationSpec.model_validate(spec)
    for role in ("llm", "stt", "tts", "realtime", "embeddings"):
        selection = spec.get(role)
        if selection is None:
            continue
        provider = connections[selection["provider_connection_uuid"]]["provider"]
        schema = REGISTRY[ServiceType[role.upper()]][provider]
        allowed = schema.model_fields.keys() - CREDENTIAL_FIELDS - CONNECTION_FIELDS
        assert set(selection["settings"]) <= allowed, (role, provider, selection)


def test_dograh_organization_becomes_one_connection_and_a_default():
    catalog = migration.OrganizationCatalog(1)
    mode, sections = migration.sections_from_v2(DOGRAH_V2)
    default = catalog.configuration(mode, sections, "Organization default")
    connections, configurations = _rows(catalog)

    assert [row["provider"] for row in connections.values()] == ["dograh"]
    (connection,) = connections.values()
    assert json.loads(connection["credentials"]) == {"api_key": "svc-key"}
    assert json.loads(connection["connection_settings"]) == {}
    assert connection["name"] == "Dograh"
    [(name, spec)] = configurations
    assert (name, catalog.configurations[0]["uuid"]) == (
        "Organization default",
        default,
    )
    assert spec["mode"] == "pipeline"
    assert {
        role: spec[role]["settings"] for role in ("llm", "stt", "tts", "embeddings")
    } == {
        "llm": {},
        "stt": {"language": "multi"},
        "tts": {"voice": "default", "speed": 1.0},
        "embeddings": {},
    }
    _assert_valid_for_registry(spec, connections)


def test_byok_pipeline_splits_credentials_connection_settings_and_settings():
    catalog = migration.OrganizationCatalog(1)
    mode, sections = migration.sections_from_v2(BYOK_PIPELINE_V2)
    catalog.configuration(mode, sections, "Organization default")
    connections, [(_, spec)] = _rows(catalog)

    by_provider = {row["provider"]: row for row in connections.values()}
    assert json.loads(by_provider["google_vertex"]["credentials"]) == {
        "credentials": "{service-account}"
    }
    assert json.loads(by_provider["google_vertex"]["connection_settings"]) == {
        "project_id": "proj",
        "location": "europe-west1",
    }
    assert by_provider["google_vertex"]["name"] == "Google Vertex"
    assert spec["llm"]["settings"] == {"model": "gemini-3.6-flash", "temperature": None}
    # Both Deepgram services share one key, so they share one connection.
    assert (
        spec["stt"]["provider_connection_uuid"]
        == spec["tts"]["provider_connection_uuid"]
    )
    assert len(connections) == 2
    # The computed TTS model is never a setting.
    assert spec["tts"]["settings"] == {"voice": "aura-2-thalia-en"}
    assert "embeddings" not in spec
    _assert_valid_for_registry(spec, connections)


def test_byok_realtime_drops_the_retired_temperature_setting():
    catalog = migration.OrganizationCatalog(1)
    mode, sections = migration.sections_from_v2(BYOK_REALTIME_V2)
    catalog.configuration(mode, sections, "Organization default")
    connections, [(_, spec)] = _rows(catalog)

    assert spec["mode"] == "realtime"
    assert spec["realtime"]["settings"] == {"model": "gemini-live", "voice": "Puck"}
    assert spec["llm"]["settings"] == {"model": "gpt-4.1-mini"}
    _assert_valid_for_registry(spec, connections)


def test_identical_setups_share_rows_and_names_stay_unique():
    catalog = migration.OrganizationCatalog(1)
    default = catalog.configuration(
        *migration.sections_from_v2(DOGRAH_V2), "Organization default"
    )
    same = catalog.configuration(*migration.sections_from_v2(DOGRAH_V2), "Support")
    assert same == default

    louder = json.loads(json.dumps(DOGRAH_V2))
    louder["dograh"]["voice"] = "rachel"
    first = catalog.configuration(*migration.sections_from_v2(louder), "Support")
    assert first != default
    louder["dograh"]["speed"] = 1.2
    second = catalog.configuration(*migration.sections_from_v2(louder), "Support")
    assert len({default, first, second}) == 3
    assert [row["name"] for row in catalog.configurations] == [
        "Organization default",
        "Support",
        "Support 2",
    ]
    # One shared key means one Dograh connection for all three.
    assert len(catalog.connections) == 1


def test_partial_overrides_merge_same_provider_and_replace_a_switched_one():
    base_mode, base = migration.sections_from_v2(DOGRAH_V2)
    mode, sections = migration.apply_partial_overrides(
        base_mode,
        base,
        {
            "llm": {"provider": "openai", "api_key": ["oa-key"], "model": "gpt-4.1"},
            "tts": {"voice": "rachel"},
        },
    )
    assert mode == "pipeline"
    assert sections["llm"] == {
        "provider": "openai",
        "api_key": ["oa-key"],
        "model": "gpt-4.1",
    }
    assert sections["tts"] == {
        "provider": "dograh",
        "api_key": "svc-key",
        "voice": "rachel",
        "speed": 1.0,
    }
    assert sections["stt"] == base["stt"]

    catalog = migration.OrganizationCatalog(1)
    catalog.configuration(mode, sections, "Mixed")
    connections, [(_, spec)] = _rows(catalog)
    _assert_valid_for_registry(spec, connections)


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm": {"provider": "openai", "model": "gpt-4.1"}},  # switched, no key
        {"is_realtime": True},  # realtime mode without a realtime service
    ],
)
def test_partial_overrides_the_catalog_cannot_represent_are_rejected(overrides):
    base_mode, base = migration.sections_from_v2(DOGRAH_V2)
    with pytest.raises(migration.Unconvertible):
        migration.apply_partial_overrides(base_mode, base, overrides)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not json",
        {"mode": "dograh", "dograh": {}},
        {
            "mode": "byok",
            "byok": {"mode": "pipeline", "pipeline": {"llm": {"provider": "openai"}}},
        },
        {"mode": "byok", "byok": {"mode": "other"}},
    ],
)
def test_incomplete_payloads_are_rejected(payload):
    with pytest.raises(migration.Unconvertible):
        migration.sections_from_v2(payload)


@pytest.fixture
async def seeded(test_engine):
    """One organization with a default, a bound workflow, drafts and an archive."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        organization = OrganizationModel(provider_id=f"catalog-backfill-{uuid4()}")
        session.add(organization)
        await session.flush()
        organization_id = organization.id
        session.add(
            OrganizationConfigurationModel(
                organization_id=organization_id,
                key="MODEL_CONFIGURATION_V2",
                value=DOGRAH_V2,
            )
        )

        def workflow(name, status, configurations):
            row = WorkflowModel(
                organization_id=organization_id,
                name=name,
                status=status,
                workflow_configurations=configurations,
            )
            session.add(row)
            return row

        def definition(workflow_row, status, configurations, *, is_current=False):
            row = WorkflowDefinitionModel(
                workflow_id=workflow_row.id,
                workflow_json={},
                workflow_configurations=configurations,
                status=status,
                is_current=is_current,
            )
            session.add(row)
            return row

        inherits = workflow("Inherits", "active", {"max_call_duration": 60})
        overridden = workflow(
            "Sales", "active", {"model_configuration_v2_override": BYOK_PIPELINE_V2}
        )
        archived = workflow(
            "Old", "archived", {"model_configuration_v2_override": BYOK_PIPELINE_V2}
        )
        await session.flush()
        published = definition(
            overridden,
            "published",
            {"model_configuration_v2_override": BYOK_PIPELINE_V2},
            is_current=True,
        )
        draft = definition(
            overridden, "draft", {"model_overrides": {"tts": {"voice": "rachel"}}}
        )
        historical = definition(
            overridden,
            "archived",
            {"model_configuration_v2_override": BYOK_REALTIME_V2},
        )
        broken = definition(
            inherits, "draft", {"model_overrides": {"llm": {"provider": "openai"}}}
        )
        await session.flush()
        overridden.released_definition_id = published.id
        await session.commit()
        ids = {
            "organization": organization_id,
            "inherits": inherits.id,
            "overridden": overridden.id,
            "archived": archived.id,
            "published": published.id,
            "draft": draft.id,
            "historical": historical.id,
            "broken": broken.id,
        }
    try:
        yield session_factory, ids
    finally:
        async with session_factory() as session:
            workflows = select(WorkflowModel.id).where(
                WorkflowModel.organization_id == organization_id
            )
            await session.execute(
                update(WorkflowModel)
                .where(WorkflowModel.organization_id == organization_id)
                .values(released_definition_id=None)
            )
            for model in (WorkflowDefinitionModel,):
                await session.execute(
                    model.__table__.delete().where(model.workflow_id.in_(workflows))
                )
            for model in (
                WorkflowModel,
                NamedModelConfigurationModel,
                ProviderConnectionModel,
                OrganizationConfigurationModel,
            ):
                await session.execute(
                    model.__table__.delete().where(
                        model.organization_id == organization_id
                    )
                )
            await session.execute(
                OrganizationModel.__table__.delete().where(
                    OrganizationModel.id == organization_id
                )
            )
            await session.commit()


async def _run(test_engine):
    async with test_engine.begin() as connection:
        return await connection.run_sync(migration.migrate)


@pytest.mark.asyncio
async def test_backfill_binds_current_rows_once_and_leaves_history_alone(
    test_engine, seeded
):
    session_factory, ids = seeded
    organization_id = ids["organization"]

    summary = await _run(test_engine)
    assert summary["organizations_without_default"] == []
    assert summary["inherited"] == [
        (
            "workflow_definitions",
            ids["broken"],
            "llm_override_without_provider_credentials",
        )
    ]

    async with session_factory() as session:
        default = await session.scalar(
            select(OrganizationConfigurationModel.value).where(
                OrganizationConfigurationModel.organization_id == organization_id,
                OrganizationConfigurationModel.key
                == "MODEL_CONFIGURATION_DEFAULT_UUID",
            )
        )
        configurations = {
            row.uuid: row
            for row in await session.scalars(
                select(NamedModelConfigurationModel).where(
                    NamedModelConfigurationModel.organization_id == organization_id
                )
            )
        }
        connections = {
            row.uuid: {"provider": row.provider, "credentials": row.credentials}
            for row in await session.scalars(
                select(ProviderConnectionModel).where(
                    ProviderConnectionModel.organization_id == organization_id
                )
            )
        }
        workflows = {
            row.id: row.workflow_configurations
            for row in await session.scalars(
                select(WorkflowModel).where(
                    WorkflowModel.organization_id == organization_id
                )
            )
        }
        definitions = {
            row.id: row.workflow_configurations
            for row in await session.scalars(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id.in_(workflows)
                )
            )
        }

    assert default in configurations
    assert configurations[default].name == "Organization default"
    # Dograh default, the Sales BYOK setup, and the draft's voice variant.
    assert sorted(row.name for row in configurations.values()) == [
        "Organization default",
        "Sales",
        "Sales 2",
    ]
    assert sorted(row["provider"] for row in connections.values()) == [
        "deepgram",
        "dograh",
        "google_vertex",
    ]
    for row in configurations.values():
        _assert_valid_for_registry(row.configuration, connections)

    binding = workflows[ids["overridden"]]["model_configuration_override"]
    assert configurations[binding["model_configuration_uuid"]].name == "Sales"
    assert definitions[ids["published"]]["model_configuration_override"] == binding
    assert "model_configuration_v2_override" in workflows[ids["overridden"]]
    draft_binding = definitions[ids["draft"]]["model_configuration_override"]
    assert configurations[draft_binding["model_configuration_uuid"]].name == "Sales 2"
    assert configurations[draft_binding["model_configuration_uuid"]].configuration[
        "tts"
    ]["settings"] == {"voice": "rachel", "speed": 1.0}
    for untouched in (
        workflows[ids["inherits"]],
        workflows[ids["archived"]],
        definitions[ids["historical"]],
        definitions[ids["broken"]],
    ):
        assert "model_configuration_override" not in untouched

    # A second run finds the default and the bindings already in place.
    again = await _run(test_engine)
    assert again["organizations"] == 0
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NamedModelConfigurationModel)
                .where(NamedModelConfigurationModel.organization_id == organization_id)
            )
            == 3
        )
