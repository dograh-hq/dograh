"""index parked queued runs for whatsapp permission lookups

Revision ID: b7d2f04c8a15
Revises: e1a2b3c4d5e6
Create Date: 2026-09-12 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2f04c8a15"
down_revision: Union[str, None] = "e1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # get_queued_runs_awaiting_whatsapp_permission filters on retry_reason with
    # no campaign_id, so none of the existing (campaign_id, ...) indexes apply.
    # state = 'queued' is not selective on its own, which left the lookup
    # scanning queued_runs on every inbound permission webhook.
    op.create_index(
        "idx_queued_runs_retry_reason_parked",
        "queued_runs",
        ["retry_reason"],
        unique=False,
        postgresql_where=sa.text("state = 'queued' AND retry_reason IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_queued_runs_retry_reason_parked", table_name="queued_runs")
