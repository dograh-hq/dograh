"""add public access token expiry

Revision ID: ad8c170ada8f
Revises: 00b0201ad918
Create Date: 2026-07-12 20:01:51.572061

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from api.constants import PUBLIC_DOWNLOAD_TOKEN_TTL_DAYS


# revision identifiers, used by Alembic.
revision: str = "ad8c170ada8f"
down_revision: Union[str, None] = "00b0201ad918"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "public_access_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Backfill previously-permanent tokens with a finite lifetime tied to the
    # run's age: runs older than the TTL are already expired, recent ones get a
    # short grace window before their public links stop working.
    op.execute(
        sa.text(
            "UPDATE workflow_runs "
            "SET public_access_token_expires_at = "
            "created_at + make_interval(days => :ttl) "
            "WHERE public_access_token IS NOT NULL "
            "AND public_access_token_expires_at IS NULL"
        ).bindparams(ttl=PUBLIC_DOWNLOAD_TOKEN_TTL_DAYS)
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "public_access_token_expires_at")
