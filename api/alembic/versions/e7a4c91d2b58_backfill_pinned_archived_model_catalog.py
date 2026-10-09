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
It only considers versions a non-terminal campaign can still run (created,
syncing, running, paused): an explicit numeric pin, or the workflow's
released definition when the campaign has no traffic split or a variant
tracks latest. That released definition is what dispatch runs, including
when the workflow itself is archived and b3c1d9e4f7a2 skipped it. Completed
and failed campaigns are left alone.

A partial override is layered on the organization's current catalog default,
including that default's llm_fallback policy, because the new binding
replaces the default rather than inheriting from it. The older
MODEL_CONFIGURATION_V2 row is used only when that default was never
created; otherwise a default the organization changed later would be
replaced by the stale credential. A JSON null model_configuration_override
is unbound, the same as a missing key, so those rows are included. An
explicit binding, including an empty object, is left as written. Rows that
already have a binding are skipped, and an identical provider setup reuses
the existing connection and named configuration, so the revision is safe to
run more than once. Inline keys stay on the row as audit data.

Downgrade is a no-op. Older code ignores the binding and still reads the
inline keys this revision leaves in place.
"""

import importlib.util
import json
import uuid
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

# Numeric pins, plus the released definition a latest variant actually
# runs. campaign_split treats any falsy traffic_split (missing, null,
# false, 0, "", [], {}) as "no split". A variant tracks latest only when
# workflow_definition_id is JSON null: dispatch reads that key directly, so
# a missing key fails the call instead of selecting the released definition.
# An archived workflow's released definition was skipped by b3c1d9e4f7a2.
_VARIANT_ARRAY_SQL = """
CASE
    WHEN jsonb_typeof(
        COALESCE(c.orchestrator_metadata, '{}')::jsonb
        #> '{traffic_split,variants}'
    ) = 'array'
    THEN COALESCE(c.orchestrator_metadata, '{}')::jsonb
         #> '{traffic_split,variants}'
    ELSE '[]'::jsonb
END
"""

_PINNED_DEFINITIONS_SQL = f"""
WITH campaign_variants AS (
    SELECT c.organization_id,
           variant
    FROM campaigns c
    CROSS JOIN LATERAL jsonb_array_elements({_VARIANT_ARRAY_SQL}) AS variant
    WHERE c.state::text IN ({_STATES_SQL})
),
explicit_pins AS (
    SELECT DISTINCT organization_id,
           (variant->>'workflow_definition_id')::int AS definition_id
    FROM campaign_variants
    WHERE jsonb_typeof(variant->'workflow_definition_id') = 'number'
),
latest_workflows AS (
    SELECT c.organization_id, c.workflow_id
    FROM campaigns c
    CROSS JOIN LATERAL (
        SELECT COALESCE(c.orchestrator_metadata, '{{}}')::jsonb -> 'traffic_split'
            AS split
    ) meta
    WHERE c.state::text IN ({_STATES_SQL})
      AND (
            meta.split IS NULL
         OR meta.split IN (
                'null'::jsonb,
                'false'::jsonb,
                '0'::jsonb,
                '""'::jsonb,
                '[]'::jsonb,
                '{{}}'::jsonb
            )
      )
    UNION
    SELECT organization_id, (variant->>'workflow_id')::int AS workflow_id
    FROM campaign_variants
    WHERE jsonb_typeof(variant->'workflow_id') = 'number'
      AND jsonb_typeof(variant->'workflow_definition_id') = 'null'
),
released_pins AS (
    SELECT DISTINCT w.organization_id,
           COALESCE(w.released_definition_id, current_def.id) AS definition_id
    FROM latest_workflows lw
    JOIN workflows w
      ON w.id = lw.workflow_id
     AND w.organization_id = lw.organization_id
    LEFT JOIN LATERAL (
        SELECT d.id
        FROM workflow_definitions d
        WHERE d.workflow_id = w.id
          AND d.is_current
          AND w.released_definition_id IS NULL
        ORDER BY d.id
        LIMIT 1
    ) current_def ON true
    WHERE COALESCE(w.released_definition_id, current_def.id) IS NOT NULL
),
campaign_pins AS (
    SELECT organization_id, definition_id FROM explicit_pins
    UNION
    SELECT organization_id, definition_id FROM released_pins
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
  AND (
        NOT jsonb_exists(
            COALESCE(d.workflow_configurations, '{{}}')::jsonb,
            :binding_key
        )
        OR jsonb_typeof(
            COALESCE(d.workflow_configurations, '{{}}')::jsonb -> :binding_key
        ) = 'null'
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


class _NoCatalogDefault:
    """The organization has no MODEL_CONFIGURATION_DEFAULT_UUID row."""


_NO_CATALOG_DEFAULT = _NoCatalogDefault()


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _configuration_value(connection, organization_id, key):
    return connection.execute(
        sa.text(
            "SELECT value FROM organization_configurations "
            "WHERE organization_id = :organization_id AND key = :key"
        ),
        {"organization_id": organization_id, "key": key},
    ).first()


def _services_from_catalog_default(connection, organization_id):
    """Rebuild the frozen converter's service dicts from the live default.

    Resolution of an unbound definition reads this catalog row, not the V2
    payload the original backfill copied it from. Partial overrides have to
    start here so a credential the organization has since rotated stays in
    place, and only the inline voice or model settings change.
    """
    row = _configuration_value(connection, organization_id, _v3.DEFAULT_KEY)
    if row is None:
        return _NO_CATALOG_DEFAULT
    configuration_uuid = _json_value(row.value)
    if not isinstance(configuration_uuid, str) or not configuration_uuid.strip():
        return None
    spec_row = connection.execute(
        sa.text(
            "SELECT configuration FROM model_configurations "
            "WHERE organization_id = :organization_id AND uuid = :uuid AND is_active"
        ),
        {
            "organization_id": organization_id,
            "uuid": configuration_uuid.strip(),
        },
    ).first()
    spec = _obj(spec_row.configuration) if spec_row is not None else None
    mode = spec.get("mode") if spec else None
    if mode not in _v3._ROLES:
        return None

    connections = {
        item.uuid: item
        for item in connection.execute(
            sa.text(
                "SELECT uuid, provider, credentials, connection_settings "
                "FROM provider_connections "
                "WHERE organization_id = :organization_id AND is_active"
            ),
            {"organization_id": organization_id},
        )
    }
    sections = {}
    for role in _v3._ROLES[mode]:
        selection = spec.get(role)
        if not isinstance(selection, dict):
            continue
        connection_uuid = selection.get("provider_connection_uuid")
        stored = connections.get(connection_uuid)
        if stored is None or not stored.provider:
            return None
        settings = selection.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        sections[role] = {
            "provider": stored.provider,
            **(_obj(stored.credentials) or {}),
            **(_obj(stored.connection_settings) or {}),
            **settings,
        }
    try:
        mode, sections = _v3._complete(mode, sections)
    except _v3.Unconvertible:
        return None
    fallback = spec.get("llm_fallback")
    if not isinstance(fallback, dict):
        fallback = None
    else:
        fallback = json.loads(json.dumps(fallback))
    return mode, sections, fallback


def _organization_services(connection, organization_id):
    current = _services_from_catalog_default(connection, organization_id)
    if current is not _NO_CATALOG_DEFAULT:
        # A default row that cannot be read must not fall back to the stale
        # V2 credential. The partial override then keeps inheriting.
        return current
    row = _configuration_value(connection, organization_id, _v3.V2_KEY)
    if row is None:
        return None
    try:
        mode, sections = _v3.sections_from_v2(row.value)
    except _v3.Unconvertible:
        return None
    return mode, sections, None


def _sections(configuration, organization_services):
    full = _obj(configuration.get(_v3.FULL_OVERRIDE_KEY))
    partial = _obj(configuration.get(_v3.PARTIAL_OVERRIDE_KEY))
    if full:
        mode, sections = _v3.sections_from_v2(full)
        # A full inline override replaces the organization default. It does
        # not inherit that default's fallback policy.
        return mode, sections, None
    if partial:
        if organization_services is None:
            raise _v3.Unconvertible("partial_override_without_organization_default")
        base_mode, base_sections, fallback = organization_services
        mode, sections = _v3.apply_partial_overrides(base_mode, base_sections, partial)
        return mode, sections, fallback
    return None


def _configuration(catalog, mode, sections, name, fallback):
    """Named configuration for these services, keeping the default's fallback.

    The frozen catalog builder does not know about llm_fallback. A binding
    replaces the organization default, so a partial override has to carry
    that policy forward or the next call drops it.
    """
    parts = [
        (role, _v3.split_service(role, service)) for role, service in sections.items()
    ]
    spec = {"version": 3, "mode": mode}
    for role, (provider, credentials, connection_settings, settings) in parts:
        spec[role] = {
            "provider_connection_uuid": catalog._connection(
                provider, credentials, connection_settings
            ),
            "settings": settings,
        }
    if fallback is not None:
        spec["llm_fallback"] = fallback
    key = _v3._canonical(spec)
    if key not in catalog._configurations:
        row_uuid = str(uuid.uuid4())
        catalog._configurations[key] = row_uuid
        catalog.configurations.append(
            {
                "uuid": row_uuid,
                "organization_id": catalog.organization_id,
                "name": catalog._name("configuration", name),
                "configuration": json.dumps(spec),
            }
        )
    return catalog._configurations[key]


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
                mode, sections, fallback = compiled
                configuration_uuid = _configuration(
                    catalog,
                    mode,
                    sections,
                    row.name or f"Workflow {row.workflow_id}",
                    fallback,
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
