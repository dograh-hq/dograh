"""migrate gemini-2.5-flash configurations to gemini-3.5-flash

Revision ID: b41f7c9d2e05
Revises: 00b0201ad918
Create Date: 2026-08-03 12:00:00.000000

"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b41f7c9d2e05"
down_revision: Union[str, None] = "00b0201ad918"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Google no longer grants new users access to the Gemini 2.5 Flash family, so
# those model ids were dropped from GOOGLE_MODELS / GOOGLE_VERTEX_MODELS. Stored
# configurations keep working until the key behind them is reissued, so this
# migration moves them onto the 3.5 equivalents.
#
# Only Google's own access paths are rewritten: `google` (AI Studio) and
# `google_vertex`. OpenRouter's `google/gemini-2.5-flash` slug is a separate
# access path that Google's gating does not cover, and it is left untouched.
#
# Run history (workflow_runs, workflow_run_text_sessions, webhook_deliveries)
# records what actually executed and is deliberately not rewritten.

_MODEL_MIGRATIONS = {
    "gemini-2.5-flash": "gemini-3.5-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
}
_MIGRATED_PROVIDERS = {"google", "google_vertex"}

# (table, json column). Every one of these holds configuration in one of three
# shapes — org/user v1 (`{"llm": {...}}`), workflow v2 override
# (`{"model_configuration_v2_override": {"byok": {...}}}`), or the legacy
# `model_overrides` block. The walker keys off `{provider, model}` pairs rather
# than fixed paths so it covers all three without enumerating them.
_CONFIGURATION_TARGETS = (
    ("organization_configurations", "value"),
    ("user_configurations", "configuration"),
    ("workflows", "workflow_configurations"),
    ("workflow_definitions", "workflow_configurations"),
)

# QA nodes inside the agent graph carry a bare `qa_model` string; the provider
# comes from the resolved configuration, so there is no provider to match on.
# These columns can hold lone UTF-16 surrogates that make a ::jsonb cast fail,
# so they are rewritten as text rather than parsed.
_GRAPH_TARGETS = (
    ("workflows", "workflow_definition"),
    ("workflow_definitions", "workflow_json"),
)


def _migrate_models(node, mapping: dict[str, str]) -> bool:
    """Rewrite Google model ids in place. Returns True if anything changed."""
    changed = False
    if isinstance(node, dict):
        if node.get("provider") in _MIGRATED_PROVIDERS and node.get("model") in mapping:
            node["model"] = mapping[node["model"]]
            changed = True
        for value in node.values():
            changed |= _migrate_models(value, mapping)
    elif isinstance(node, list):
        for item in node:
            changed |= _migrate_models(item, mapping)
    return changed


def _rewrite_configurations(connection, mapping: dict[str, str]) -> None:
    for table, column in _CONFIGURATION_TARGETS:
        rows = (
            connection.execute(
                sa.text(
                    f"SELECT id, {column} AS payload FROM {table} "
                    f"WHERE {column}::text LIKE :needle ORDER BY id"
                ),
                {"needle": "%gemini-2.5-flash%"},
            )
            .mappings()
            .all()
        )

        updated = 0
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            if not isinstance(payload, (dict, list)):
                continue
            if not _migrate_models(payload, mapping):
                continue
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = CAST(:payload AS json) "
                    "WHERE id = :id"
                ),
                {"payload": json.dumps(payload), "id": row["id"]},
            )
            updated += 1

        print(f"{table}.{column}: rewrote {updated} of {len(rows)} candidate row(s)")


def _rewrite_graph_qa_models(connection, mapping: dict[str, str]) -> None:
    for table, column in _GRAPH_TARGETS:
        for old_model, new_model in mapping.items():
            result = connection.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = CAST("
                    f"  regexp_replace({column}::text, :pattern, :replacement, 'g')"
                    " AS json) "
                    f"WHERE {column}::text ~ :pattern"
                ),
                {
                    "pattern": r'("qa_model"\s*:\s*)"'
                    + old_model.replace(".", r"\.")
                    + '"',
                    "replacement": r'\1"' + new_model + '"',
                },
            )
            print(
                f"{table}.{column}: rewrote qa_model {old_model} -> {new_model} "
                f"in {result.rowcount} row(s)"
            )


def upgrade() -> None:
    connection = op.get_bind()
    _rewrite_configurations(connection, _MODEL_MIGRATIONS)
    _rewrite_graph_qa_models(connection, _MODEL_MIGRATIONS)


def downgrade() -> None:
    # Not reversible: after the upgrade a gemini-3.5-flash configuration written
    # by this migration is indistinguishable from one a user chose themselves,
    # so reversing the mapping would downgrade unrelated configurations onto a
    # model Google no longer grants access to.
    pass
