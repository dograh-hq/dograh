"""Backfill the model catalog from V2 organization and workflow model settings.

Revision ID: b3c1d9e4f7a2
Revises: a7128e93bc40

Each organization's MODEL_CONFIGURATION_V2 row becomes provider connections
plus an "Organization default" named configuration, which becomes the
organization's default. Each active workflow row, its current published
definition and its drafts that carry an inline V2 override or partial
model_overrides get a named configuration of their own (identical setups
within an organization share one row) and a model_configuration_override
binding. Rows without inline settings get no binding, which the application
reads as "inherit the organization default". Archived workflows and
historical definitions are left untouched.

The conversion is a frozen copy of the application's V2 compile and
credential split at this revision, operating on raw JSON, so the migration
never imports application code. Credentials are carried over unvalidated: a
dead key fails at call time exactly as it did before. The few settings keys
the catalog no longer accepts are dropped (_DROP_SETTINGS); any other unknown
key surfaces as a 422 at call start, and the original payloads stay on every
row for repair.

Downgrade is a no-op: older code ignores the binding and still reads the
retained inline keys.
"""

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "b3c1d9e4f7a2"
down_revision = "a7128e93bc40"
branch_labels = None
depends_on = None

V2_KEY = "MODEL_CONFIGURATION_V2"
DEFAULT_KEY = "MODEL_CONFIGURATION_DEFAULT_UUID"
BINDING_KEY = "model_configuration_override"
FULL_OVERRIDE_KEY = "model_configuration_v2_override"
PARTIAL_OVERRIDE_KEY = "model_overrides"

# Frozen copies of api.schemas.model_connections at this revision.
CREDENTIAL_FIELDS = frozenset(
    {"api_key", "credentials", "aws_access_key", "aws_secret_key", "aws_session_token"}
)
CONNECTION_FIELDS = frozenset(
    {
        "base_url",
        "endpoint",
        "project_id",
        "location",
        "aws_region",
        "region",
        "api_version",
        "group_id",
        "bill_to",
    }
)
# Persisted by older dumps but rejected by the catalog: computed fields, and a
# field these realtime schemas have since dropped.
_DROP_SETTINGS = {
    ("tts", "deepgram"): frozenset({"model"}),
    ("tts", "xai"): frozenset({"model"}),
    ("realtime", "google_realtime"): frozenset({"temperature"}),
    ("realtime", "google_vertex_realtime"): frozenset({"temperature"}),
}
_ROLES = {
    "pipeline": ("llm", "stt", "tts", "embeddings"),
    "realtime": ("llm", "realtime", "embeddings"),
}
_REQUIRED = {"pipeline": ("llm", "stt", "tts"), "realtime": ("llm", "realtime")}

_LEGACY_ROWS_SQL = """
SELECT w.organization_id,
       w.id AS workflow_id,
       w.name,
       'workflows' AS kind,
       w.id AS row_id,
       w.workflow_configurations AS configuration
FROM workflows w
WHERE w.status <> 'archived'
  AND jsonb_exists_any(COALESCE(w.workflow_configurations, '{}')::jsonb,
                       ARRAY[:full_key, :partial_key])
UNION ALL
SELECT w.organization_id,
       w.id,
       w.name,
       'workflow_definitions',
       d.id,
       d.workflow_configurations
FROM workflow_definitions d
JOIN workflows w ON w.id = d.workflow_id
WHERE w.status <> 'archived'
  AND (d.status = 'draft' OR (d.status = 'published' AND d.is_current))
  AND jsonb_exists_any(COALESCE(d.workflow_configurations, '{}')::jsonb,
                       ARRAY[:full_key, :partial_key])
ORDER BY 1, 2, 4, 5
"""

_INSERT_CONNECTION_SQL = """
INSERT INTO provider_connections
    (uuid, organization_id, name, provider, credentials, connection_settings,
     revision, is_active, created_at, updated_at)
VALUES
    (:uuid, :organization_id, :name, :provider, :credentials, :connection_settings,
     1, true, now(), now())
"""

_INSERT_CONFIGURATION_SQL = """
INSERT INTO model_configurations
    (uuid, organization_id, name, configuration, revision, is_active,
     created_at, updated_at)
VALUES
    (:uuid, :organization_id, :name, :configuration, 1, true, now(), now())
"""

_INSERT_DEFAULT_SQL = """
INSERT INTO organization_configurations
    (organization_id, key, value, created_at, updated_at)
VALUES
    (:organization_id, :key, :value, now(), now())
"""


class Unconvertible(ValueError):
    """A payload the catalog cannot represent; the row keeps inheriting."""


def _as_object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _complete(mode, sections):
    if any(role not in sections for role in _REQUIRED[mode]):
        raise Unconvertible(f"incomplete_{mode}_configuration")
    return mode, {role: sections[role] for role in _ROLES[mode] if role in sections}


def sections_from_v2(value):
    """Return (mode, {role: service}) for a V2 payload.

    Mirrors compile_ai_model_configuration_v2: Dograh mode expands the single
    managed key into one service per role; BYOK mode already stores a service
    per role.
    """
    value = _as_object(value)
    if value is None:
        raise Unconvertible("not_an_object")
    if value.get("mode") == "dograh":
        managed = _as_object(value.get("dograh")) or {}
        if not managed.get("api_key"):
            raise Unconvertible("missing_dograh_service_key")
        shared = {"provider": "dograh", "api_key": managed["api_key"]}
        llm = dict(shared)
        if managed.get("temperature") is not None:
            llm["temperature"] = managed["temperature"]
        return "pipeline", {
            "llm": llm,
            "stt": {**shared, "language": managed.get("language", "multi")},
            "tts": {
                **shared,
                "voice": managed.get("voice", "default"),
                "speed": managed.get("speed", 1.0),
            },
            "embeddings": dict(shared),
        }
    if value.get("mode") == "byok":
        byok = _as_object(value.get("byok")) or {}
        mode = byok.get("mode")
        if mode not in _ROLES:
            raise Unconvertible("unknown_byok_mode")
        group = _as_object(byok.get(mode))
        if group is None:
            raise Unconvertible("missing_byok_sections")
        return _complete(
            mode,
            {
                role: dict(group[role])
                for role in _ROLES[mode]
                if isinstance(group.get(role), dict)
            },
        )
    raise Unconvertible("unknown_mode")


def apply_partial_overrides(mode, sections, overrides):
    """Layer legacy partial model_overrides onto the organization's services.

    Mirrors resolve_effective_config: a section is replaced when its provider
    changes (so it must carry its own credentials) and merged otherwise.
    """
    overrides = _as_object(overrides)
    if overrides is None:
        raise Unconvertible("partial_override_not_an_object")
    merged = {role: dict(service) for role, service in sections.items()}
    if "is_realtime" in overrides:
        mode = "realtime" if overrides["is_realtime"] else "pipeline"
    for role in ("llm", "stt", "tts", "realtime"):
        override = _as_object(overrides.get(role))
        if override is None:
            continue
        base = merged.get(role)
        if base is None or (
            "provider" in override and override["provider"] != base.get("provider")
        ):
            if not override.get("provider") or not (
                override.keys() & CREDENTIAL_FIELDS
            ):
                raise Unconvertible(f"{role}_override_without_provider_credentials")
            merged[role] = dict(override)
        else:
            merged[role] = {**base, **override}
    return _complete(mode, merged)


def split_service(role, service):
    """Split one service into provider, credentials, connection settings, settings."""
    service = dict(service)
    provider = service.pop("provider", None)
    if not isinstance(provider, str) or not provider:
        raise Unconvertible(f"{role}_without_provider")
    dropped = _DROP_SETTINGS.get((role, provider), frozenset())
    credentials = {k: v for k, v in service.items() if k in CREDENTIAL_FIELDS}
    connection = {k: v for k, v in service.items() if k in CONNECTION_FIELDS}
    settings = {
        k: v
        for k, v in service.items()
        if k not in CREDENTIAL_FIELDS
        and k not in CONNECTION_FIELDS
        and k not in dropped
    }
    return provider, credentials, connection, settings


class OrganizationCatalog:
    """Rows to insert for one organization; identical setups share a row."""

    def __init__(self, organization_id):
        self.organization_id = organization_id
        self.connections = []
        self.configurations = []
        self._connections = {}
        self._configurations = {}
        self._names = {"connection": set(), "configuration": set()}

    def _name(self, kind, wanted):
        wanted = (wanted or "").strip()[:120] or kind.title()
        name, suffix = wanted, 2
        while name in self._names[kind]:
            name, suffix = f"{wanted} {suffix}", suffix + 1
        self._names[kind].add(name)
        return name

    def _connection(self, provider, credentials, connection_settings):
        key = (provider, _canonical(credentials), _canonical(connection_settings))
        if key not in self._connections:
            row_uuid = str(uuid.uuid4())
            self._connections[key] = row_uuid
            self.connections.append(
                {
                    "uuid": row_uuid,
                    "organization_id": self.organization_id,
                    "name": self._name(
                        "connection", provider.replace("_", " ").title()
                    ),
                    "provider": provider,
                    "credentials": json.dumps(credentials),
                    "connection_settings": json.dumps(connection_settings),
                }
            )
        return self._connections[key]

    def configuration(self, mode, sections, name):
        """Register a named configuration for these services; returns its uuid."""
        parts = [
            (role, split_service(role, service)) for role, service in sections.items()
        ]
        spec = {"version": 3, "mode": mode}
        for role, (provider, credentials, connection_settings, settings) in parts:
            spec[role] = {
                "provider_connection_uuid": self._connection(
                    provider, credentials, connection_settings
                ),
                "settings": settings,
            }
        key = _canonical(spec)
        if key not in self._configurations:
            row_uuid = str(uuid.uuid4())
            self._configurations[key] = row_uuid
            self.configurations.append(
                {
                    "uuid": row_uuid,
                    "organization_id": self.organization_id,
                    "name": self._name("configuration", name),
                    "configuration": json.dumps(spec),
                }
            )
        return self._configurations[key]


def migrate(connection):
    """Run the backfill on a synchronous connection and return a summary.

    Organizations that already have a default, and rows that already carry a
    binding, are left alone, so the backfill is safe to apply more than once.
    """
    summary = {
        "organizations": 0,
        "defaults": 0,
        "connections": 0,
        "configurations": 0,
        "bindings": 0,
        "inherited": [],
        "organizations_without_default": [],
    }
    configured = {
        row.organization_id
        for row in connection.execute(
            sa.text(
                "SELECT organization_id FROM organization_configurations WHERE key = :key"
            ),
            {"key": DEFAULT_KEY},
        )
    }
    v2_rows = connection.execute(
        sa.text(
            "SELECT organization_id, value FROM organization_configurations "
            "WHERE key = :key ORDER BY organization_id"
        ),
        {"key": V2_KEY},
    ).all()

    catalogs, defaults, organization_services = {}, {}, {}
    for row in v2_rows:
        if row.organization_id in configured:
            continue
        catalog = OrganizationCatalog(row.organization_id)
        catalogs[row.organization_id] = catalog
        try:
            mode, sections = sections_from_v2(row.value)
            defaults[row.organization_id] = catalog.configuration(
                mode, sections, "Organization default"
            )
            organization_services[row.organization_id] = (mode, sections)
        except Unconvertible as exc:
            summary["organizations_without_default"].append(
                (row.organization_id, str(exc))
            )
    if not catalogs:
        return summary

    bindings = {"workflows": [], "workflow_definitions": []}
    legacy_rows = connection.execute(
        sa.text(_LEGACY_ROWS_SQL),
        {"full_key": FULL_OVERRIDE_KEY, "partial_key": PARTIAL_OVERRIDE_KEY},
    ).all()
    for row in legacy_rows:
        catalog = catalogs.get(row.organization_id)
        configuration = _as_object(row.configuration)
        if (
            catalog is None
            or configuration is None
            or configuration.get(BINDING_KEY) is not None
        ):
            continue
        try:
            full = _as_object(configuration.get(FULL_OVERRIDE_KEY))
            partial = _as_object(configuration.get(PARTIAL_OVERRIDE_KEY))
            if full:
                mode, sections = sections_from_v2(full)
            elif partial:
                if row.organization_id not in organization_services:
                    raise Unconvertible("partial_override_without_organization_default")
                base_mode, base_sections = organization_services[row.organization_id]
                mode, sections = apply_partial_overrides(
                    base_mode, base_sections, partial
                )
            else:
                continue  # The key is present but empty: the row inherits.
            configuration_uuid = catalog.configuration(
                mode, sections, row.name or f"Workflow {row.workflow_id}"
            )
        except Unconvertible as exc:
            summary["inherited"].append((row.kind, row.row_id, str(exc)))
            continue
        configuration[BINDING_KEY] = {"model_configuration_uuid": configuration_uuid}
        bindings[row.kind].append(
            {"id": row.row_id, "configuration": json.dumps(configuration)}
        )

    connections = [c for catalog in catalogs.values() for c in catalog.connections]
    configurations = [
        c for catalog in catalogs.values() for c in catalog.configurations
    ]
    if connections:
        connection.execute(sa.text(_INSERT_CONNECTION_SQL), connections)
    if configurations:
        connection.execute(sa.text(_INSERT_CONFIGURATION_SQL), configurations)
    if defaults:
        connection.execute(
            sa.text(_INSERT_DEFAULT_SQL),
            [
                {
                    "organization_id": organization_id,
                    "key": DEFAULT_KEY,
                    "value": json.dumps(configuration_uuid),
                }
                for organization_id, configuration_uuid in defaults.items()
            ],
        )
    for table, rows in bindings.items():
        if rows:
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET workflow_configurations = :configuration "
                    "WHERE id = :id"
                ),
                rows,
            )
    summary.update(
        organizations=len(catalogs),
        defaults=len(defaults),
        connections=len(connections),
        configurations=len(configurations),
        bindings=sum(len(rows) for rows in bindings.values()),
    )
    return summary


def upgrade() -> None:
    summary = migrate(op.get_bind())
    print(
        "Model catalog backfill: "
        f"{summary['organizations']} organization(s), "
        f"{summary['defaults']} default(s), "
        f"{summary['connections']} connection(s), "
        f"{summary['configurations']} configuration(s), "
        f"{summary['bindings']} workflow binding(s)"
    )
    for organization_id, reason in summary["organizations_without_default"]:
        print(f"  organization {organization_id} has no default: {reason}")
    for kind, row_id, reason in summary["inherited"]:
        print(f"  {kind} {row_id} inherits the organization default: {reason}")


def downgrade() -> None:
    # Older code ignores model_configuration_override and still reads the
    # inline keys this migration leaves in place.
    pass
