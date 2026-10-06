"""Normalize organization V2 and current workflow model settings into the catalog.

Planning is pure and never probes providers, rotates credentials, or touches
knowledge-base indexes. The database client owns the transaction and locks. Old
published/history payloads remain readable during the compatibility window; the
original model payloads remain in place for audit, including superseded draft
settings. Never log these private plans or Pydantic validation exceptions.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException

from api.schemas.ai_model_configuration import (
    EffectiveAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.schemas.model_configuration_migration import (
    MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY,
    MODEL_CONFIGURATION_DEFAULT_KEY,
    MODEL_CONFIGURATION_MIGRATION_KEY,
    WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY,
    ModelConfigurationMigrationPlan,
    ModelConfigurationMigrationSource,
)
from api.services.configuration.resolve import resolve_effective_config

_OLD_MODEL_KEYS = ("model_overrides", "model_configuration_v2_override")


class ModelConfigurationMigrationError(ValueError):
    """Safe, fixed error code; never wrap the underlying credential-bearing data."""


def _migration_uuid(organization_id: int, source: str) -> str:
    # No secret-dependent fingerprints are persisted or exposed as identifiers.
    return str(
        uuid5(NAMESPACE_URL, f"dograh:model-catalog:v3:{organization_id}:{source}")
    )


def _validate_effective(configuration: EffectiveAIModelConfiguration) -> None:
    required = (
        ("llm", "realtime") if configuration.is_realtime else ("llm", "stt", "tts")
    )
    if any(getattr(configuration, role) is None for role in required):
        raise ModelConfigurationMigrationError("incomplete_model_configuration")
    from api.services.configuration.masking import check_for_masked_keys
    from api.services.configuration.model_connections import (
        validate_service_configuration,
    )

    try:
        check_for_masked_keys(configuration)
    except ValueError:
        raise ModelConfigurationMigrationError(
            "masked_credentials_require_review"
        ) from None
    dograh_pools = []
    for role in (*required, "embeddings"):
        service = getattr(configuration, role)
        if service is not None:
            try:
                validate_service_configuration(service)
            except (HTTPException, ValueError):
                raise ModelConfigurationMigrationError(
                    "invalid_service_configuration"
                ) from None
        if service is not None and service.provider == "dograh":
            keys = service.get_all_api_keys()
            if not keys or any(not key for key in keys):
                raise ModelConfigurationMigrationError("missing_dograh_service_key")
            dograh_pools.append(set(keys))
    if dograh_pools and not set.intersection(*dograh_pools):
        raise ModelConfigurationMigrationError("incompatible_dograh_service_keys")


class _CatalogImporter:
    def __init__(self, source, plan):
        self.source = source
        self.plan = plan

    def _connection(self, service, identity: str | None) -> str:
        # Shared with CRUD and the runtime resolver so field classification is
        # identical, including key arrays, Vertex/AWS auth, and computed fields.
        from api.services.configuration.model_connections import (
            split_service_configuration,
        )

        provider, credentials, connection_settings, _ = split_service_configuration(
            service
        )
        for row in [*self.source.connections, *self.plan.connections]:
            if (
                row.get("is_active", True)
                and row["provider"] == provider
                and row["credentials"] == credentials
                and row["connection_settings"] == connection_settings
            ):
                return row["uuid"]
        connection_uuid = (
            _migration_uuid(self.source.organization_id, f"connection:{identity}")
            if identity
            else str(uuid4())
        )
        if any(row["uuid"] == connection_uuid for row in self.source.connections):
            raise ModelConfigurationMigrationError("migration_connection_was_edited")
        self.plan.connections.append(
            {
                "uuid": connection_uuid,
                "name": f"Imported {provider} connection {len(self.source.connections) + len(self.plan.connections) + 1}",
                "provider": provider,
                "credentials": deepcopy(credentials),
                "connection_settings": deepcopy(connection_settings),
            }
        )
        return connection_uuid

    def specification(self, effective, identity: str | None) -> dict:
        from api.services.configuration.model_connections import (
            split_service_configuration,
        )

        _validate_effective(effective)
        specification = {
            "version": 3,
            "mode": "realtime" if effective.is_realtime else "pipeline",
            "embeddings": None,
        }
        roles = (
            ("llm", "realtime", "embeddings")
            if effective.is_realtime
            else ("llm", "stt", "tts", "embeddings")
        )
        for role in roles:
            service = getattr(effective, role)
            if service is None:
                continue
            connection_uuid = self._connection(
                service, f"{identity}:{role}" if identity else None
            )
            _, _, _, settings = split_service_configuration(service)
            specification[role] = {
                "provider_connection_uuid": connection_uuid,
                "settings": deepcopy(settings),
            }
        return specification

    def named_configuration(self, specification: dict, identity: str, name: str) -> str:
        for row in [*self.source.configurations, *self.plan.configurations]:
            if row.get("is_active", True) and row["configuration"] == specification:
                return row["uuid"]
        configuration_uuid = _migration_uuid(
            self.source.organization_id, f"configuration:{identity}"
        )
        if any(row["uuid"] == configuration_uuid for row in self.source.configurations):
            raise ModelConfigurationMigrationError("migration_configuration_was_edited")
        self.plan.configurations.append(
            {
                "uuid": configuration_uuid,
                "name": name,
                "configuration": deepcopy(specification),
            }
        )
        return configuration_uuid


def _organization_effective(
    source: ModelConfigurationMigrationSource,
) -> EffectiveAIModelConfiguration:
    if not source.organization_configuration:
        raise ModelConfigurationMigrationError(
            "missing_organization_configuration_requires_provisioning"
        )
    try:
        effective = compile_ai_model_configuration_v2(
            OrganizationAIModelConfigurationV2.model_validate(
                source.organization_configuration
            )
        )
        _validate_effective(effective)
        return effective
    except ModelConfigurationMigrationError:
        raise
    except (ValueError, TypeError, AttributeError):
        raise ModelConfigurationMigrationError(
            "invalid_organization_configuration"
        ) from None


def _workflow_effective(
    configuration: dict, organization_effective: EffectiveAIModelConfiguration
) -> EffectiveAIModelConfiguration:
    if configuration.get("model_configuration_v2_override"):
        effective = compile_ai_model_configuration_v2(
            OrganizationAIModelConfigurationV2.model_validate(
                configuration["model_configuration_v2_override"]
            )
        )
    else:
        effective = resolve_effective_config(
            organization_effective, configuration.get("model_overrides")
        )
    _validate_effective(effective)
    return effective


def _model_settings(configuration: dict) -> dict:
    return {
        key: deepcopy(configuration[key])
        for key in (*_OLD_MODEL_KEYS, WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY)
        if key in configuration
    }


def _with_model_binding(target: dict, binding: dict | None) -> dict:
    result = deepcopy(target)
    # Keep each row's ORIGINAL payload, even when a published configuration
    # supersedes its draft. An explicit empty V3 override means inherit the
    # current catalog default; omitting it would reactivate an old draft override.
    result[WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY] = deepcopy(binding or {})
    return result


def _embedding_issues(source, plan, effective) -> None:
    if not source.embedding_spaces:
        return
    identities = {(row["model"], row["dimension"]) for row in source.embedding_spaces}
    if len(identities) > 1:
        plan.issues.append(
            {
                "reason": "multiple_indexed_embedding_spaces_require_review",
                "space_count": len(identities),
            }
        )
    if effective.embeddings is None:
        plan.issues.append(
            {"reason": "indexed_documents_without_default_embeddings_require_review"}
        )
    elif any(model != effective.embeddings.model for model, _ in identities):
        plan.issues.append(
            {"reason": "indexed_embedding_model_differs_from_default_requires_review"}
        )
    # Never change document/index metadata or schedule reindexing here.


def build_model_configuration_migration_plan(
    source: ModelConfigurationMigrationSource,
    *,
    include_workflows: bool = True,
) -> ModelConfigurationMigrationPlan:
    """Build an idempotent, all-or-nothing organization normalization plan."""
    plan = ModelConfigurationMigrationPlan(source.organization_id)
    if include_workflows and source.migration_complete:
        plan.status = "already_migrated"
        return plan
    if not include_workflows and source.default_configuration_uuid:
        plan.status = "already_configured"
        return plan
    try:
        effective = _organization_effective(source)
        importer = _CatalogImporter(source, plan)
        specification = importer.specification(effective, "organization-default")
        default_uuid = importer.named_configuration(
            specification, "organization-default", "Organization default"
        )
        if source.default_configuration_uuid:
            # Bootstrap may have imported just the default before the explicit
            # rollout command. Continue only while it and its credentials are
            # unedited; never overwrite a user's post-bootstrap decisions.
            default_row = next(
                (
                    row
                    for row in source.configurations
                    if row["uuid"] == source.default_configuration_uuid
                ),
                None,
            )
            untouched_bootstrap = (
                (source.bootstrap_metadata or {}).get("model_configuration_uuid")
                == source.default_configuration_uuid
                and source.default_configuration_uuid == default_uuid
                and default_row is not None
                and default_row.get("revision", 1) == 1
            )
            referenced = {
                selection["provider_connection_uuid"]
                for selection in specification.values()
                if isinstance(selection, dict)
            }
            untouched_bootstrap = untouched_bootstrap and all(
                row.get("revision", 1) == 1
                for row in source.connections
                if row["uuid"] in referenced
            )
            if not untouched_bootstrap:
                raise ModelConfigurationMigrationError(
                    "catalog_already_edited_requires_review"
                )
        else:
            plan.organization_updates[MODEL_CONFIGURATION_DEFAULT_KEY] = default_uuid
            plan.organization_updates[MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY] = {
                "model_configuration_uuid": default_uuid
            }
        _embedding_issues(source, plan, effective)
        if not include_workflows:
            return plan

        for workflow in sorted(source.workflows, key=lambda item: item.id):
            released = next(
                (
                    definition
                    for definition in workflow.definitions
                    if definition.id == workflow.released_definition_id
                ),
                None,
            )
            if workflow.released_definition_id is not None and released is None:
                raise ModelConfigurationMigrationError(
                    "released_definition_ownership_or_reference_invalid"
                )
            baseline = released.configuration if released else workflow.configuration
            source_ids = {"workflow_id": workflow.id}
            if released:
                source_ids["definition_id"] = released.id
            else:
                plan.issues.append(
                    {
                        **source_ids,
                        "reason": "no_released_definition_using_workflow_settings",
                    }
                )
            historical = [
                definition
                for definition in workflow.definitions
                if definition.status != "draft" and definition is not released
            ]
            plan.historical_definitions_retained += len(historical)
            binding = baseline.get(WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY)
            if binding is None and (
                baseline.get("model_configuration_v2_override")
                or baseline.get("model_overrides")
            ):
                try:
                    workflow_effective = _workflow_effective(baseline, effective)
                except (ValueError, TypeError, AttributeError):
                    if baseline.get("model_configuration_v2_override"):
                        raise ModelConfigurationMigrationError(
                            "invalid_full_workflow_configuration_requires_review"
                        ) from None
                    workflow_effective = effective
                    plan.issues.append(
                        {
                            **source_ids,
                            "reason": "invalid_legacy_override_fallback_to_organization_default",
                        }
                    )
                workflow_specification = importer.specification(
                    workflow_effective, f"workflow:{workflow.id}"
                )
                configuration_uuid = importer.named_configuration(
                    workflow_specification,
                    f"workflow:{workflow.id}",
                    f"Workflow {workflow.id} models",
                )
                binding = {"model_configuration_uuid": configuration_uuid}
            if (
                workflow.configuration.get(WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY)
                is not None
            ):
                plan.issues.append(
                    {
                        "workflow_id": workflow.id,
                        "reason": "workflow_already_uses_catalog_preserved",
                    }
                )
            else:
                normalized = _with_model_binding(workflow.configuration, binding)
                if normalized != workflow.configuration:
                    plan.workflow_updates.append((workflow.id, normalized))
            targets = [
                definition
                for definition in workflow.definitions
                if definition.status == "draft" or definition is released
            ]
            for definition in targets:
                if (
                    definition.configuration.get(
                        WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY
                    )
                    is not None
                ):
                    plan.issues.append(
                        {
                            "workflow_id": workflow.id,
                            "definition_id": definition.id,
                            "reason": "workflow_already_uses_catalog_preserved",
                        }
                    )
                    continue
                if definition.status == "draft" and _model_settings(
                    definition.configuration
                ) != _model_settings(baseline):
                    plan.issues.append(
                        {
                            "workflow_id": workflow.id,
                            "definition_id": definition.id,
                            "reason": "draft_model_settings_reset_to_released",
                        }
                    )
                normalized = _with_model_binding(definition.configuration, binding)
                if normalized != definition.configuration:
                    plan.definition_updates.append((definition.id, normalized))
        plan.organization_updates[MODEL_CONFIGURATION_MIGRATION_KEY] = {
            "status": "completed",
            "version": 3,
        }
        return plan
    except ModelConfigurationMigrationError as exc:
        # Discard every accumulated mutation on any organization-level blocker.
        return ModelConfigurationMigrationPlan(
            source.organization_id,
            status="blocked",
            issues=[{"reason": str(exc)}],
        )


async def normalize_organization_model_configurations(
    organization_id: int, *, apply: bool = False
) -> dict:
    from api.db import db_client

    plan = await db_client.run_model_configuration_migration_transaction(
        organization_id,
        build_model_configuration_migration_plan,
        apply=apply,
        include_workflows=True,
    )
    return plan.report()


async def ensure_organization_model_catalog(organization_id: int) -> bool:
    """Bootstrap catalog from existing V2 credentials; never mint another key."""
    from api.db import db_client

    plan = await db_client.run_model_configuration_migration_transaction(
        organization_id,
        lambda source: build_model_configuration_migration_plan(
            source, include_workflows=False
        ),
        apply=True,
        include_workflows=False,
    )
    return plan.status in ("applied", "already_configured")


async def ensure_legacy_workflow_model_configuration(
    organization_id: int, workflow_configurations: dict
) -> dict | None:
    """Import connections idempotently and return a private V3 spec, never a binding.

    This compatibility bridge does not edit a workflow, definition, org default,
    or existing catalog row. Unlike the operator migration, invalid legacy
    overrides fail rather than silently switching an executing call's provider.
    """
    if not any(workflow_configurations.get(key) for key in _OLD_MODEL_KEYS):
        return None
    from api.db import db_client

    def planner(source):
        plan = ModelConfigurationMigrationPlan(organization_id)
        try:
            effective = _workflow_effective(
                workflow_configurations, _organization_effective(source)
            )
            plan.imported_configuration = _CatalogImporter(source, plan).specification(
                effective, None
            )
        except (ValueError, TypeError, AttributeError):
            raise ModelConfigurationMigrationError(
                "invalid_legacy_workflow_configuration"
            ) from None
        return plan

    plan = await db_client.run_model_configuration_migration_transaction(
        organization_id,
        planner,
        apply=True,
        include_workflows=False,
    )
    return plan.imported_configuration
