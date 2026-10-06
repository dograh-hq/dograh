"""Private, in-memory values used by the catalog normalization command.

These objects contain credentials. Only ``MigrationPlan.report`` is suitable for
operator output; the private source/plan objects must never be serialized/logged.
"""

from dataclasses import dataclass, field
from typing import Any

MODEL_CONFIGURATION_DEFAULT_KEY = "MODEL_CONFIGURATION_DEFAULT_UUID"
MODEL_CONFIGURATION_MIGRATION_KEY = "MODEL_CONFIGURATION_NORMALIZATION_V3"
MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY = "MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_V3"
WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY = "model_configuration_override"


@dataclass(repr=False)
class MigrationDefinition:
    id: int
    workflow_id: int
    status: str
    configuration: dict
    is_current: bool = False


@dataclass(repr=False)
class MigrationWorkflow:
    id: int
    configuration: dict
    released_definition_id: int | None = None
    definitions: list[MigrationDefinition] = field(default_factory=list)


@dataclass(repr=False)
class ModelConfigurationMigrationSource:
    organization_id: int
    organization_configuration: dict | None = None
    default_configuration_uuid: str | None = None
    migration_complete: bool = False
    bootstrap_metadata: dict | None = None
    connections: list[dict] = field(default_factory=list)
    configurations: list[dict] = field(default_factory=list)
    workflows: list[MigrationWorkflow] = field(default_factory=list)
    # Only identities, dimensions and counts; no document text or vectors.
    embedding_spaces: list[dict] = field(default_factory=list)


@dataclass(repr=False)
class ModelConfigurationMigrationPlan:
    organization_id: int
    status: str = "planned"
    connections: list[dict] = field(default_factory=list)
    configurations: list[dict] = field(default_factory=list)
    workflow_updates: list[tuple[int, dict]] = field(default_factory=list)
    definition_updates: list[tuple[int, dict]] = field(default_factory=list)
    organization_updates: dict[str, Any] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    historical_definitions_retained: int = 0
    # Runtime compatibility import returns this privately, never in its report.
    imported_configuration: dict | None = None

    def report(self) -> dict:
        """Return the sole public representation, containing no source payloads."""
        return {
            "organization_id": self.organization_id,
            "status": self.status,
            "provider_connections_created": len(self.connections),
            "model_configurations_created": len(self.configurations),
            "workflows_updated": len(self.workflow_updates),
            "definitions_updated": len(self.definition_updates),
            "historical_definitions_retained": self.historical_definitions_retained,
            "issues": self.issues,
        }
