"""index whatsapp permission message id and workflow_runs queued_run_id

Revision ID: c3f5a1b90d47
Revises: b7d2f04c8a15
Create Date: 2026-09-12 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f5a1b90d47"
down_revision: Union[str, None] = "b7d2f04c8a15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # update_whatsapp_call_permission_status_by_message_id resolves a row by
    # meta_message_id alone on every permission-message status and reply
    # webhook, and none of the indexes created with the table cover that
    # column. meta_message_id is set once an outbound request message is sent
    # and is never cleared afterward, so the partial predicate does not limit
    # this to outstanding requests -- it only excludes rows that never had a
    # message id attached. Not unique on purpose: the webhook path should
    # degrade to an extra row rather than a write failure if Meta ever
    # replays a wamid.
    op.create_index(
        "ix_whatsapp_perm_meta_message_id",
        "whatsapp_call_permissions",
        ["meta_message_id"],
        unique=False,
        postgresql_where=sa.text("meta_message_id IS NOT NULL"),
    )

    # workflow_runs.queued_run_id was added as a plain foreign key in
    # fefdd1835b7d; Postgres does not index the referencing side, so every
    # per-lead lookup (get_workflow_run_by_queued_run_id and the campaign
    # client's equivalents) scanned workflow_runs. created_at trails the key so
    # the index also serves the "latest run" ORDER BY without a sort. Partial
    # because only campaign-dispatched runs carry a queued_run_id and every
    # caller uses equality, which never matches NULL.
    op.create_index(
        "idx_workflow_runs_queued_run_id",
        "workflow_runs",
        ["queued_run_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("queued_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_workflow_runs_queued_run_id", table_name="workflow_runs")
    op.drop_index(
        "ix_whatsapp_perm_meta_message_id", table_name="whatsapp_call_permissions"
    )
