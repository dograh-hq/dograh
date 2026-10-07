"""Behavioral coverage for the catalog normalization and compatibility importer."""

import json
from copy import deepcopy

import pytest

from api.schemas.ai_model_configuration import (
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.schemas.model_configuration_migration import (
    MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY,
    MODEL_CONFIGURATION_DEFAULT_KEY,
    MODEL_CONFIGURATION_MIGRATION_KEY,
    MigrationDefinition,
    MigrationWorkflow,
    ModelConfigurationMigrationSource,
)
from api.services.configuration import model_configuration_migration as migration
from api.services.configuration.registry import REGISTRY, ServiceType


def _dograh(key="private-service-key", **settings):
    return {"version": 2, "mode": "dograh", "dograh": {"api_key": key, **settings}}


def _pipeline(**llm_settings):
    return {
        "version": 2,
        "mode": "byok",
        "byok": {
            "mode": "pipeline",
            "pipeline": {
                "llm": {
                    "provider": "openai",
                    "api_key": ["first-key", "second-key"],
                    "model": "gpt-4.1-mini",
                    **llm_settings,
                },
                "tts": {
                    "provider": "elevenlabs",
                    "api_key": "tts-key",
                    "model": "eleven_flash_v2_5",
                    "voice": "voice-id",
                },
                "stt": {
                    "provider": "deepgram",
                    "api_key": "stt-key",
                    "model": "nova-2-phonecall",
                },
            },
        },
    }


def _source(configuration=None, **kwargs):
    return ModelConfigurationMigrationSource(
        organization_id=42,
        organization_configuration=configuration or _dograh(),
        **kwargs,
    )


def _rehydrate(plan, configuration):
    connections = {connection["uuid"]: connection for connection in plan.connections}
    services = {}
    for role in ("llm", "stt", "tts", "realtime", "embeddings"):
        selection = configuration.get(role)
        if selection is None:
            continue
        connection = connections[selection["provider_connection_uuid"]]
        schema = REGISTRY[ServiceType[role.upper()]][connection["provider"]]
        services[role] = schema.model_validate(
            {
                "provider": connection["provider"],
                **connection["credentials"],
                **connection["connection_settings"],
                **selection["settings"],
            }
        )
    return services


@pytest.mark.parametrize(
    "configuration",
    [_dograh(temperature=None), _pipeline(), _pipeline(temperature=None)],
)
def test_effective_parameters_and_pooled_credentials_survive(configuration):
    source = _source(configuration)
    plan = migration.build_model_configuration_migration_plan(source)
    assert plan.status == "planned"
    original = compile_ai_model_configuration_v2(
        OrganizationAIModelConfigurationV2.model_validate(configuration)
    )
    for role, service in _rehydrate(
        plan, plan.configurations[0]["configuration"]
    ).items():
        assert service.model_dump(mode="json") == getattr(original, role).model_dump(
            mode="json"
        )
    serialized = json.dumps(plan.report())
    assert "private-service-key" not in serialized
    assert "first-key" not in serialized
    assert "voice-id" not in serialized


def test_dograh_role_connections_deduplicate_without_minting_keys():
    plan = migration.build_model_configuration_migration_plan(_source())
    assert len(plan.connections) == 1
    assert plan.connections[0]["credentials"] == {"api_key": "private-service-key"}
    spec = plan.configurations[0]["configuration"]
    assert {
        spec[role]["provider_connection_uuid"]
        for role in ("llm", "stt", "tts", "embeddings")
    } == {plan.connections[0]["uuid"]}
    other_org = _source()
    other_org.organization_id = 43
    other = migration.build_model_configuration_migration_plan(other_org)
    assert plan.connections[0]["uuid"] != other.connections[0]["uuid"]


def test_published_model_settings_win_without_changing_draft_nonmodel_content():
    released = MigrationDefinition(
        10,
        1,
        "published",
        {
            "model_configuration_v2_override": _dograh(temperature=0.4),
            "recording": True,
        },
    )
    draft = MigrationDefinition(
        11,
        1,
        "draft",
        {
            "model_overrides": {"llm": {"temperature": 0.9}},
            "recording": False,
            "prompt": "private draft prompt",
            "nested": {"keep": True},
        },
    )
    archived = MigrationDefinition(
        12, 1, "archived", {"model_overrides": {"llm": {"temperature": 0.8}}}
    )
    workflow = MigrationWorkflow(
        1,
        {"model_overrides": {"llm": {"temperature": 0.6}}, "custom": "preserve"},
        10,
        [released, draft, archived],
    )
    source = _source(workflows=[workflow])
    original = deepcopy(source)
    plan = migration.build_model_configuration_migration_plan(source)
    definitions = dict(plan.definition_updates)
    assert set(definitions) == {10, 11}
    assert definitions[11]["prompt"] == "private draft prompt"
    assert definitions[11]["recording"] is False
    assert definitions[11]["nested"] == {"keep": True}
    assert definitions[11]["model_overrides"] == draft.configuration["model_overrides"]
    for definition in (released, draft):
        assert {
            key: value
            for key, value in definitions[definition.id].items()
            if key != "model_configuration_override"
        } == definition.configuration
    assert (
        definitions[11]["model_configuration_override"]
        == definitions[10]["model_configuration_override"]
    )
    assert dict(plan.workflow_updates)[1]["custom"] == "preserve"
    assert source == original
    assert plan.historical_definitions_retained == 1
    assert any(
        issue["reason"] == "draft_model_settings_reset_to_released"
        for issue in plan.issues
    )
    assert "private draft prompt" not in json.dumps(plan.report())


def test_inheritance_is_preserved_while_explicit_equal_override_is_consolidated():
    inherited = MigrationWorkflow(
        1,
        {},
        10,
        [
            MigrationDefinition(10, 1, "published", {}),
            MigrationDefinition(
                11, 1, "draft", {"model_overrides": {"llm": {"temperature": 0.9}}}
            ),
        ],
    )
    explicit = MigrationWorkflow(
        2,
        {},
        20,
        [
            MigrationDefinition(
                20, 2, "published", {"model_configuration_v2_override": _dograh()}
            )
        ],
    )
    plan = migration.build_model_configuration_migration_plan(
        _source(workflows=[inherited, explicit])
    )
    assert dict(plan.definition_updates)[11] == {
        **inherited.definitions[1].configuration,
        "model_configuration_override": {},
    }
    assert dict(plan.definition_updates)[10] == {"model_configuration_override": {}}
    assert len(plan.configurations) == 1
    assert (
        dict(plan.definition_updates)[20]["model_configuration_override"][
            "model_configuration_uuid"
        ]
        == plan.organization_updates[MODEL_CONFIGURATION_DEFAULT_KEY]
    )


def test_unresolvable_partial_override_falls_back_and_reports_source_ids():
    baseline = {
        "model_overrides": {"llm": {"provider": "google", "model": "gemini-2.5-flash"}}
    }
    workflow = MigrationWorkflow(
        9, {}, 90, [MigrationDefinition(90, 9, "published", baseline)]
    )
    plan = migration.build_model_configuration_migration_plan(
        _source(workflows=[workflow])
    )
    assert len(plan.configurations) == 1
    assert {
        "workflow_id": 9,
        "definition_id": 90,
        "reason": "invalid_legacy_override_fallback_to_organization_default",
    } in plan.issues
    assert (
        dict(plan.definition_updates)[90]["model_configuration_override"][
            "model_configuration_uuid"
        ]
        == plan.organization_updates[MODEL_CONFIGURATION_DEFAULT_KEY]
    )


def test_missing_organization_default_is_flagged_without_importing_member_keys():
    plan = migration.build_model_configuration_migration_plan(
        ModelConfigurationMigrationSource(5836)
    )
    assert plan.status == "blocked"
    assert plan.connections == plan.configurations == []
    assert plan.issues == [
        {"reason": "missing_organization_configuration_requires_provisioning"}
    ]


def test_invalid_full_override_blocks_transaction_and_drops_earlier_planned_writes():
    workflow = MigrationWorkflow(
        1, {"model_configuration_v2_override": {"mode": "byok"}}
    )
    plan = migration.build_model_configuration_migration_plan(
        _source(workflows=[workflow])
    )
    assert plan.status == "blocked"
    assert not plan.connections
    assert not plan.configurations
    assert not plan.organization_updates


def test_repeated_normalization_never_overwrites_subsequent_edits():
    source = _source(migration_complete=True)
    source.organization_configuration = {"invalid": "post-migration source edit"}
    plan = migration.build_model_configuration_migration_plan(source)
    assert plan.status == "already_migrated"
    assert not plan.organization_updates
    assert not plan.connections


def test_default_only_bootstrap_can_be_followed_by_workflow_migration():
    source = _source(
        workflows=[
            MigrationWorkflow(1, {"model_overrides": {"llm": {"temperature": 0.3}}})
        ]
    )
    bootstrap = migration.build_model_configuration_migration_plan(
        source, include_workflows=False
    )
    assert not bootstrap.workflow_updates
    assert MODEL_CONFIGURATION_MIGRATION_KEY not in bootstrap.organization_updates
    source.connections = bootstrap.connections
    source.configurations = bootstrap.configurations
    source.default_configuration_uuid = bootstrap.organization_updates[
        MODEL_CONFIGURATION_DEFAULT_KEY
    ]
    source.bootstrap_metadata = bootstrap.organization_updates[
        MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY
    ]
    plan = migration.build_model_configuration_migration_plan(source)
    assert plan.status == "planned"
    assert len(plan.workflow_updates) == 1
    assert MODEL_CONFIGURATION_DEFAULT_KEY not in plan.organization_updates
    assert MODEL_CONFIGURATION_MIGRATION_KEY in plan.organization_updates
    source.configurations[0]["revision"] = 2
    blocked = migration.build_model_configuration_migration_plan(source)
    assert blocked.status == "blocked"
    assert not blocked.workflow_updates


def test_embedding_mismatches_are_reported_without_modifying_index_data():
    source = _source(
        embedding_spaces=[
            {"model": "old-embedding", "dimension": 1536},
            {"model": "other-embedding", "dimension": 768},
        ]
    )
    original = deepcopy(source)
    plan = migration.build_model_configuration_migration_plan(source)
    assert source == original
    assert {issue["reason"] for issue in plan.issues} == {
        "multiple_indexed_embedding_spaces_require_review",
        "indexed_embedding_model_differs_from_default_requires_review",
    }


def test_realtime_auxiliary_llm_survives_and_ignored_temperature_does_not_activate():
    configuration = {
        "version": 2,
        "mode": "byok",
        "byok": {
            "mode": "realtime",
            "realtime": {
                "llm": {
                    "provider": "openai",
                    "api_key": "llm-key",
                    "model": "gpt-4.1-mini",
                },
                "realtime": {
                    "provider": "google_realtime",
                    "api_key": "realtime-key",
                    "model": "gemini-3.1-flash-live-preview",
                    "temperature": 1.9,
                },
            },
        },
    }
    plan = migration.build_model_configuration_migration_plan(_source(configuration))
    spec = plan.configurations[0]["configuration"]
    assert spec["mode"] == "realtime"
    assert "llm" in spec and "realtime" in spec
    assert "temperature" not in spec["realtime"]["settings"]
    assert "stt" not in spec and "tts" not in spec


@pytest.mark.asyncio
async def test_runtime_legacy_import_deduplicates_without_changing_saved_configuration(
    monkeypatch,
):
    from api.db import db_client

    source = _source()
    plans = []

    async def transaction(organization_id, planner, **kwargs):
        assert organization_id == source.organization_id
        assert kwargs == {"apply": True, "include_workflows": False}
        plan = planner(source)
        plans.append(plan)
        source.connections.extend(deepcopy(plan.connections))
        return plan

    monkeypatch.setattr(
        db_client, "run_model_configuration_migration_transaction", transaction
    )
    legacy = {"model_overrides": {"llm": {"temperature": 0.3}}}
    original = deepcopy(legacy)
    first = await migration.ensure_legacy_workflow_model_configuration(42, legacy)
    second = await migration.ensure_legacy_workflow_model_configuration(42, legacy)
    assert first == second
    assert legacy == original
    assert len(plans[0].connections) == 1
    assert plans[1].connections == []
    assert not any(
        plan.organization_updates
        or plan.workflow_updates
        or plan.definition_updates
        or plan.configurations
        for plan in plans
    )


@pytest.mark.asyncio
async def test_runtime_legacy_import_rejects_invalid_override_without_fallback(
    monkeypatch,
):
    from api.db import db_client

    async def transaction(organization_id, planner, **kwargs):
        return planner(_source())

    monkeypatch.setattr(
        db_client, "run_model_configuration_migration_transaction", transaction
    )
    with pytest.raises(
        migration.ModelConfigurationMigrationError,
        match="^invalid_legacy_workflow_configuration$",
    ):
        await migration.ensure_legacy_workflow_model_configuration(
            42,
            {
                "model_overrides": {
                    "llm": {
                        "provider": "google",
                        "api_key": "secret",
                        "temperature": "invalid",
                    }
                }
            },
        )


def test_cli_defaults_to_read_only_and_requires_explicit_apply():
    from scripts.normalize_model_configurations import build_parser

    assert build_parser().parse_args([]).apply is False
    assert build_parser().parse_args(["--apply"]).apply is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--apply", "--dry-run"])


@pytest.mark.parametrize(
    "llm",
    [
        {
            "provider": "google_vertex",
            "project_id": "private-project",
            "location": "us-central1",
            "credentials": '{"private_key":"private-service-account"}',
        },
        {
            "provider": "google_vertex",
            "project_id": "private-project",
            "location": "global",
        },
        {
            "provider": "aws_bedrock",
            "aws_access_key": "private-access-key",
            "aws_secret_key": "private-secret-key",
            "aws_region": "us-west-2",
        },
        {
            "provider": "speaches",
            "base_url": "https://203.0.113.1/v1",
            "model": "local-model",
            "api_key": None,
        },
    ],
)
def test_non_api_key_and_credentialless_auth_shapes_round_trip(llm, monkeypatch):
    from api.utils import url_security

    monkeypatch.setattr(url_security, "DEPLOYMENT_MODE", "oss")
    configuration = _pipeline()
    configuration["byok"]["pipeline"]["llm"] = llm
    plan = migration.build_model_configuration_migration_plan(_source(configuration))
    assert plan.status == "planned"
    expected = compile_ai_model_configuration_v2(
        OrganizationAIModelConfigurationV2.model_validate(configuration)
    )
    actual = _rehydrate(plan, plan.configurations[0]["configuration"])["llm"]
    assert actual.model_dump(mode="json") == expected.llm.model_dump(mode="json")
    serialized = json.dumps(plan.report())
    assert "private" not in serialized
    assert "local-model" not in serialized


def test_semantically_empty_required_credentials_block_org():
    configuration = _pipeline()
    configuration["byok"]["pipeline"]["llm"]["api_key"] = ""
    plan = migration.build_model_configuration_migration_plan(_source(configuration))
    assert plan.status == "blocked"
    assert not plan.connections


@pytest.mark.parametrize(
    "edit", ["default_selection", "named_settings", "credential_rotation", "archive"]
)
def test_normalization_does_not_reset_catalog_edits_after_bootstrap(edit):
    source = _source(
        workflows=[
            MigrationWorkflow(1, {"model_overrides": {"llm": {"temperature": 0.3}}})
        ]
    )
    bootstrap = migration.build_model_configuration_migration_plan(
        source, include_workflows=False
    )
    source.connections = deepcopy(bootstrap.connections)
    source.configurations = deepcopy(bootstrap.configurations)
    source.default_configuration_uuid = bootstrap.organization_updates[
        MODEL_CONFIGURATION_DEFAULT_KEY
    ]
    source.bootstrap_metadata = bootstrap.organization_updates[
        MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY
    ]
    if edit == "default_selection":
        source.default_configuration_uuid = "user-selected-configuration"
    elif edit == "named_settings":
        source.configurations[0]["configuration"]["llm"]["settings"]["temperature"] = (
            0.8
        )
        source.configurations[0]["revision"] = 2
    elif edit == "credential_rotation":
        source.connections[0]["credentials"]["api_key"] = "rotated-private-key"
        source.connections[0]["revision"] = 2
    else:
        source.connections[0]["is_active"] = False
        source.connections[0]["revision"] = 2
    expected = deepcopy(source)
    plan = migration.build_model_configuration_migration_plan(source)
    assert plan.status == "blocked"
    assert not plan.connections and not plan.configurations
    assert not plan.workflow_updates and not plan.definition_updates
    assert not plan.organization_updates
    assert source == expected


def test_all_drafts_inherit_published_selection_without_erasing_original_payloads():
    # Org 206 has this shape: the published version inherits the org default,
    # while the workflow row and one of two drafts contain BYOK configurations.
    original_override = {"model_configuration_v2_override": _pipeline()}
    workflow = MigrationWorkflow(
        260,
        deepcopy(original_override),
        1024,
        [
            MigrationDefinition(1024, 260, "published", {}),
            MigrationDefinition(1025, 260, "draft", deepcopy(original_override)),
            MigrationDefinition(1026, 260, "draft", {}),
        ],
    )
    plan = migration.build_model_configuration_migration_plan(
        _source(workflows=[workflow])
    )
    assert plan.status == "planned"
    assert len(plan.configurations) == len(plan.connections) == 1
    assert dict(plan.workflow_updates)[260] == {
        **original_override,
        "model_configuration_override": {},
    }
    updates = dict(plan.definition_updates)
    for definition in workflow.definitions:
        assert updates[definition.id] == {
            **definition.configuration,
            "model_configuration_override": {},
        }


def test_duplicate_workflow_setups_have_stable_names_and_uuids_in_any_read_order():
    workflows = [
        MigrationWorkflow(
            workflow_id,
            {"model_configuration_v2_override": _pipeline()},
        )
        for workflow_id in (2, 1)
    ]
    first = migration.build_model_configuration_migration_plan(
        _source(workflows=workflows)
    )
    second = migration.build_model_configuration_migration_plan(
        _source(workflows=list(reversed(workflows)))
    )
    assert first.connections == second.connections
    assert first.configurations == second.configurations
    assert first.workflow_updates == second.workflow_updates


@pytest.mark.parametrize("migrated_definition", ["published", "draft"])
def test_existing_catalog_binding_does_not_skip_unmigrated_workflow_rows(
    migrated_definition,
):
    published = MigrationDefinition(10, 1, "published", {})
    draft = MigrationDefinition(
        11, 1, "draft", {"model_configuration_v2_override": _pipeline()}
    )
    existing = published if migrated_definition == "published" else draft
    existing.configuration["model_configuration_override"] = {}
    workflow = MigrationWorkflow(1, {}, 10, [published, draft])
    plan = migration.build_model_configuration_migration_plan(
        _source(workflows=[workflow])
    )
    assert plan.status == "planned"
    assert dict(plan.workflow_updates)[1]["model_configuration_override"] == {}
    updates = dict(plan.definition_updates)
    assert existing.id not in updates
    remaining = draft if existing is published else published
    assert updates[remaining.id] == {
        **remaining.configuration,
        "model_configuration_override": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{}, {"model_configuration_uuid": "new-uuid"}])
async def test_catalog_binding_ignores_retired_workflow_payloads(monkeypatch, override):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
    from api.services.configuration import ai_model_configuration, model_connections
    from api.services.configuration.run_model_configuration import (
        get_workflow_model_override,
    )

    effective = EffectiveAIModelConfiguration()
    resolver = AsyncMock(return_value=SimpleNamespace(effective=effective))
    monkeypatch.setattr(model_connections, "resolve_model_configuration", resolver)
    importer = AsyncMock(side_effect=AssertionError("Retired settings were read"))
    monkeypatch.setattr(
        migration, "ensure_legacy_workflow_model_configuration", importer
    )
    configuration = {
        "model_configuration_override": override,
        "model_configuration_v2_override": {"invalid": "retired-full-config"},
        "model_overrides": {"llm": {"temperature": "retired-invalid-value"}},
    }
    assert await get_workflow_model_override(42, configuration) == override
    assert (
        await ai_model_configuration.get_effective_ai_model_configuration_for_workflow(
            organization_id=42, workflow_configurations=configuration
        )
        is effective
    )
    resolver.assert_awaited_once_with(42, workflow_override=override)
    importer.assert_not_awaited()


@pytest.mark.parametrize("override", [{}, {"model_configuration_uuid": "new-uuid"}])
def test_workflow_responses_omit_retired_payloads_without_mutating_storage(override):
    from api.services.configuration.masking import mask_workflow_configurations

    stored = {
        "model_configuration_override": override,
        "model_configuration_v2_override": _pipeline(),
        "model_overrides": {"llm": {"api_key": "retired-secret"}},
        "recording": True,
    }
    before = deepcopy(stored)
    assert mask_workflow_configurations(stored) == {
        "model_configuration_override": override,
        "recording": True,
    }
    assert stored == before
