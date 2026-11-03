"""retire the provisional_vad turn start strategy

Revision ID: c4e21b7f80a9
Revises: f3a1c47b9e02
Create Date: 2026-09-13 17:10:00.000000

"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e21b7f80a9"
down_revision: Union[str, None] = "f3a1c47b9e02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# `provisional_vad` gated the user turn start on a transcript, so the turn start
# resolved from a queued frame and the interruption it broadcast flushed the
# queued end-of-turn proposal behind it — the turn then never closed. It is
# retired in favour of `default`, which follows the STT's own turn boundaries
# when the STT reports them and falls back to transcript + VAD when it does not.
#
# `provisional_vad_pause_secs` only configured that strategy, so it goes too.
#
# Run history (workflow_runs and friends) records what actually executed and is
# deliberately not rewritten. `workflow_definitions` rows are immutable
# versions, but a run executes against the definition it points at, so they are
# rewritten here as well — otherwise a re-run of an old definition would still
# ask for the retired strategy.
_RETIRED_STRATEGY = "provisional_vad"
_REPLACEMENT_STRATEGY = "default"
_RETIRED_KEY = "provisional_vad_pause_secs"

_TARGETS = (
    ("workflows", "workflow_configurations"),
    ("workflow_definitions", "workflow_configurations"),
)


def _rewrite(config: dict) -> bool:
    """Rewrite one configuration dict in place. True if anything changed."""
    changed = False
    if config.get("turn_start_strategy") == _RETIRED_STRATEGY:
        config["turn_start_strategy"] = _REPLACEMENT_STRATEGY
        changed = True
    if _RETIRED_KEY in config:
        del config[_RETIRED_KEY]
        changed = True
    return changed


def upgrade() -> None:
    conn = op.get_bind()
    for table, column in _TARGETS:
        # These are `json`, not `jsonb`, so key containment (`?`) is unavailable
        # and a ::jsonb cast is avoided.
        #
        # The two operators are not interchangeable here. `->>` extracts the
        # value as text, which is what the strategy comparison wants, but it
        # renders a JSON null as SQL NULL — indistinguishable from a missing
        # key. Stored configs do carry explicit nulls for keys the user never
        # configured (see WorkflowConfigurationDefaults._treat_null_as_unset),
        # so the key check uses `->`, which returns the JSON value and is SQL
        # NULL only when the key is genuinely absent.
        rows = conn.execute(
            sa.text(
                f"SELECT id, {column} FROM {table} "
                f"WHERE {column} IS NOT NULL "
                f"AND ({column}->>'turn_start_strategy' = :retired "
                f"     OR {column}->'{_RETIRED_KEY}' IS NOT NULL)"
            ),
            {"retired": _RETIRED_STRATEGY},
        ).fetchall()

        for row_id, raw in rows:
            config = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(config, dict) or not _rewrite(config):
                continue
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :cfg WHERE id = :id"),
                {"cfg": json.dumps(config), "id": row_id},
            )


def downgrade() -> None:
    # One-way: the retired strategy no longer exists in the application, so
    # restoring the value would leave rows the code cannot honour. Workflows
    # that were on it now read as `default`, which is a valid configuration.
    pass
