"""add_oauth2_client_credentials

Revision ID: 1236f759fffe
Revises: 00b0201ad918
Create Date: 2026-08-01 17:02:51.904611

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1236f759fffe'
down_revision: Union[str, None] = '00b0201ad918'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from alembic_postgresql_enum import TableReference

def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="webhook_credential_type",
        new_values=[
            "none",
            "api_key",
            "bearer_token",
            "basic_auth",
            "custom_header",
            "oauth2_client_credentials",
        ],
        affected_columns=[
            TableReference(
                table_schema="public",
                table_name="external_credentials",
                column_name="credential_type",
            )
        ],
        enum_values_to_rename=[],
    )

def downgrade() -> None:
    # Safety guard: permanently deleting OAuth2 credentials (including encrypted
    # config) without operator consent is data loss. Refuse the downgrade if any
    # such rows still exist so the operator can migrate or delete them explicitly.
    conn = op.get_bind()
    conn.execute(sa.text("LOCK TABLE external_credentials IN EXCLUSIVE MODE;"))
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM external_credentials "
            "WHERE credential_type = 'oauth2_client_credentials'"
        )
    )
    count = result.scalar()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} credential row(s) with "
            "credential_type='oauth2_client_credentials' still exist. "
            "Delete or migrate them first, then re-run the downgrade."
        )
    op.sync_enum_values(
        enum_schema="public",
        enum_name="webhook_credential_type",
        new_values=[
            "none",
            "api_key",
            "bearer_token",
            "basic_auth",
            "custom_header",
        ],
        affected_columns=[
            TableReference(
                table_schema="public",
                table_name="external_credentials",
                column_name="credential_type",
            )
        ],
        enum_values_to_rename=[],
    )
