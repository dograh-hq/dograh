"""Drop workflow_run_mode Postgres enum, store mode as VARCHAR.

The Postgres enum required a migration every time a new telephony provider
was added. With the column stored as VARCHAR, new providers can be added
purely in application code (registry registration in the provider package).
The Python ``WorkflowRunMode`` enum stays as a constant set used for
comparisons; only the database column type changes.

Revision ID: 4d8e9b2a3c5f
Revises: cdcf9f65913b, f2e1d0c9b8a7
Create Date: 2026-04-25 21:30:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d8e9b2a3c5f"
down_revision: Union[str, Sequence[str], None] = (
    "cdcf9f65913b",
    "f2e1d0c9b8a7",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mode values that existed when the enum was dropped, used to recreate the
# enum on downgrade. New values added after this migration won't appear here
# — that's the point of the refactor.
_LEGACY_MODE_VALUES = (
    "ari",
    "plivo",
    "twilio",
    "vonage",
    "vobiz",
    "cloudonix",
    "telnyx",
    "webrtc",
    "smallwebrtc",
    "stasis",
    "VOICE",
    "CHAT",
)


def upgrade() -> None:
    # Convert the mode column from the workflow_run_mode enum to VARCHAR(64).
    # Postgres requires a USING expression to cast the enum to text safely.
    op.execute(
        "ALTER TABLE workflow_runs ALTER COLUMN mode TYPE VARCHAR(64) USING mode::text"
    )
    # Drop the now-unused enum type.
    op.execute("DROP TYPE workflow_run_mode")


def downgrade() -> None:
    # Recreate the enum with the values that existed at the time this
    # migration ran. Any values added afterwards (e.g. a future provider
    # registered in code only) will fail to cast back; operators on those
    # rows must clean them up before downgrading.
    enum_values = ", ".join(f"'{v}'" for v in _LEGACY_MODE_VALUES)
    op.execute(f"CREATE TYPE workflow_run_mode AS ENUM ({enum_values})")
    op.execute(
        "ALTER TABLE workflow_runs "
        "ALTER COLUMN mode TYPE workflow_run_mode USING mode::workflow_run_mode"
    )
