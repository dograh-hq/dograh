"""The catalog importer assigns readable names without renaming existing rows."""

from copy import deepcopy

from api.services.configuration.model_configuration_migration import (
    build_model_configuration_migration_plan,
)
from api.tests.test_model_configuration_migration import _dograh, _source


def test_import_names_avoid_collisions_without_changing_existing_connections():
    source = _source(_dograh())
    source.connections = [
        {
            "uuid": f"existing-{index}",
            "name": name,
            "provider": "dograh",
            "credentials": {"api_key": f"existing-key-{index}"},
            "connection_settings": {},
        }
        for index, name in enumerate(("Dograh", "Dograh 2", "Support production"))
    ]
    existing = deepcopy(source.connections)
    plan = build_model_configuration_migration_plan(source)
    assert plan.status == "planned"
    assert [row["name"] for row in plan.connections] == ["Dograh 3"]
    assert source.connections == existing

    # The source is scoped to an organization by the migration DB client.
    other = _source(_dograh())
    other.organization_id = 43
    plan = build_model_configuration_migration_plan(other)
    assert [row["name"] for row in plan.connections] == ["Dograh"]
