"""Bind archived workflow versions that non-terminal campaigns still run.

Revision ID: e7a4c91d2b58
Revises: b3c1d9e4f7a2

b3c1d9e4f7a2 already shipped. It bound active workflows and their current
published definition plus drafts, and left archived workflows and historical
definitions untouched. A campaign may still place calls on one of those
versions: the run selector accepts published and archived definitions. With
no catalog binding, resolution inherits the organization default and drops
the version's own inline voice, model and credentials.

This revision writes those missing bindings with the same frozen conversion.
It only considers versions referenced by a non-terminal campaign
(created, syncing, running, paused), including an explicit pin of an archived
definition. Completed and failed campaigns are left alone. Rows that already
have a binding are skipped, and an identical provider setup reuses the
existing connection and named configuration, so the revision is safe to run
more than once. Inline keys stay on the row as audit data.

Downgrade is a no-op. Older code ignores the binding and still reads the
inline keys this revision leaves in place.
"""

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "e7a4c91d2b58"
down_revision = "b3c1d9e4f7a2"
branch_labels = None
depends_on = None

# Campaigns in these states can still place a call on the pinned version.
NON_TERMINAL_CAMPAIGN_STATES = ("created", "syncing", "running", "paused")

_PREVIOUS_PATH = Path(__file__).with_name("b3c1d9e4f7a2_backfill_model_catalog_v3.py")
_spec = importlib.util.spec_from_file_location(
    "backfill_model_catalog_v3_frozen", _PREVIOUS_PATH
)
_v3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_v3)

_STATES_SQL = ", ".join(f"'{state}'" for state in NON_TERMINAL_CAMPAIGN_STATES)

# Explicit pins only. Campaigns that track "latest" already received a binding
# for an active workflow's current definition in b3c1d9e4f7a2; a missing pin
# means that version, not an archived one.
_PINNED_DEFINITIONS_SQL = f"""
WITH campaign_pins AS (
    SELECT DISTINCT c.organization_id,
           (variant->>'workflow_definition_id')::int AS definition_id
    FROM campaigns c
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(
                COALESCE(c.orchestrator_metadata, '{{}}')::jsonb
                #> '{{traffic_split,variants}}'
            ) = 'array'
            THEN COALESCE(c.orchestrator_metadata, '{{}}')::jsonb
                 #> '{{traffic_split,variants}}'
            ELSE '[]'::jsonb
        END
    ) AS variant
    WHERE c.state::text IN ({_STATES_SQL})
      AND jsonb_typeof(variant->'workflow_definition_id') = 'number'
)
SELECT w.organization_id,
       w.id AS workflow_id,
       w.name,
       d.id AS row_id,
       d.workflow_configurations AS configuration
FROM campaign_pins p
JOIN workflow_definitions d ON d.id = p.definition_id
JOIN workflows w
  ON w.id = d.workflow_id
 AND w.organization_id = p.organization_id
WHERE jsonb_exists_any(
          COALESCE(d.workflow_configurations, '{{}}')::jsonb,
          ARRAY[:full_key, :partial_key]
      )
  AND NOT jsonb_exists(
          COALESCE(d.workflow_configurations, '{{}}')::jsonb,
          :binding_key
      )
ORDER BY w.organization_id, d.id
"""


def _obj(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _seed_existing(connection, catalog, organization_id):
    """Teach the catalog about rows b3c1d9e4f7a2 already inserted.

    Matching setups keep their uuid. Only genuinely new connections and
    configurations are appended for insert.
    """
    connections = connection.execute(
        sa.text(
            "SELECT uuid, name, provider, credentials, connection_settings "
            "FROM provider_connections "
            "WHERE organization_id = :organization_id AND is_active"
        ),
        {"organization_id": organization_id},
    ).all()
    for row in connections:
        credentials = _obj(row.credentials) or {}
        settings = _obj(row.connection_settings) or {}
        key = (
            row.provider,
            _v3._canonical(credentials),
            _v3._canonical(settings),
        )
        catalog._connections[key] = row.uuid
        if row.name:
            catalog._names["connection"].add(row.name)

    configurations = connection.execute(
        sa.text(
            "SELECT uuid, name, configuration FROM model_configurations "
            "WHERE organization_id = :organization_id AND is_active"
        ),
        {"organization_id": organization_id},
    ).all()
    for row in configurations:
        spec = _obj(row.configuration)
        if spec is None:
            continue
        catalog._configurations[_v3._canonical(spec)] = row.uuid
        if row.name:
            catalog._names["configuration"].add(row.name)


def _organization_services(connection, organization_id):
    row = connection.execute(
        sa.text(
            "SELECT value FROM organization_configurations "
            "WHERE organization_id = :organization_id AND key = :key"
        ),
        {"organization_id": organization_id, "key": _v3.V2_KEY},
    ).first()
    if row is None:
        return None
    try:
        return _v3.sections_from_v2(row.value)
    except _v3.Unconvertible:
        return None


def _sections(configuration, organization_services):
    full = _obj(configuration.get(_v3.FULL_OVERRIDE_KEY))
    partial = _obj(configuration.get(_v3.PARTIAL_OVERRIDE_KEY))
    if full:
        return _v3.sections_from_v2(full)
    if partial:
        if organization_services is None:
            raise _v3.Unconvertible("partial_override_without_organization_default")
        base_mode, base_sections = organization_services
        return _v3.apply_partial_overrides(base_mode, base_sections, partial)
    return None


def migrate(connection):
    """Bind pinned archived versions. Returns a summary; safe to repeat."""
    summary = {
        "organizations": 0,
        "connections": 0,
        "configurations": 0,
        "bindings": 0,
        "inherited": [],
    }
    rows = connection.execute(
        sa.text(_PINNED_DEFINITIONS_SQL),
        {
            "full_key": _v3.FULL_OVERRIDE_KEY,
            "partial_key": _v3.PARTIAL_OVERRIDE_KEY,
            "binding_key": _v3.BINDING_KEY,
        },
    ).all()
    by_organization = {}
    for row in rows:
        by_organization.setdefault(row.organization_id, []).append(row)
    if not by_organization:
        return summary

    new_connections = []
    new_configurations = []
    bindings = []
    for organization_id, org_rows in by_organization.items():
        catalog = _v3.OrganizationCatalog(organization_id)
        _seed_existing(connection, catalog, organization_id)
        organization_services = _organization_services(connection, organization_id)
        for row in org_rows:
            configuration = _obj(row.configuration)
            if configuration is None or configuration.get(_v3.BINDING_KEY) is not None:
                continue
            try:
                compiled = _sections(configuration, organization_services)
                if compiled is None:
                    continue
                mode, sections = compiled
                configuration_uuid = catalog.configuration(
                    mode, sections, row.name or f"Workflow {row.workflow_id}"
                )
            except _v3.Unconvertible as exc:
                summary["inherited"].append(
                    ("workflow_definitions", row.row_id, str(exc))
                )
                continue
            configuration[_v3.BINDING_KEY] = {
                "model_configuration_uuid": configuration_uuid
            }
            bindings.append(
                {"id": row.row_id, "configuration": json.dumps(configuration)}
            )
        new_connections.extend(catalog.connections)
        new_configurations.extend(catalog.configurations)

    if new_connections:
        connection.execute(sa.text(_v3._INSERT_CONNECTION_SQL), new_connections)
    if new_configurations:
        connection.execute(sa.text(_v3._INSERT_CONFIGURATION_SQL), new_configurations)
    if bindings:
        connection.execute(
            sa.text(
                "UPDATE workflow_definitions "
                "SET workflow_configurations = :configuration WHERE id = :id"
            ),
            bindings,
        )
    summary.update(
        organizations=len(by_organization),
        connections=len(new_connections),
        configurations=len(new_configurations),
        bindings=len(bindings),
    )
    return summary


def upgrade() -> None:
    summary = migrate(op.get_bind())
    print(
        "Pinned archived model catalog backfill: "
        f"{summary['organizations']} organization(s), "
        f"{summary['connections']} connection(s), "
        f"{summary['configurations']} configuration(s), "
        f"{summary['bindings']} workflow binding(s)"
    )
    for kind, row_id, reason in summary["inherited"]:
        print(f"  {kind} {row_id} inherits the organization default: {reason}")


def downgrade() -> None:
    # The binding is additive. Older code ignores it and reads the inline keys
    # that this revision does not remove.
    pass
