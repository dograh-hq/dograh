"""add_tool_marketplace

Revision ID: 237bb541375d
Revises: 91cc6ba3e1c7
Create Date: 2026-07-06 18:12:35.891305

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '237bb541375d'
down_revision: Union[str, None] = '91cc6ba3e1c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tool_marketplace',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('subcategory', sa.String(length=50), nullable=True),
        sa.Column('icon', sa.String(length=10), nullable=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('tool_category', sa.String(length=50), nullable=False, server_default='mcp'),
        sa.Column('config_template', postgresql.JSONB(), nullable=False),
        sa.Column('oauth_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('oauth_auth_url', sa.String(length=500), nullable=True),
        sa.Column('oauth_token_url', sa.String(length=500), nullable=True),
        sa.Column('oauth_scopes', sa.String(length=500), nullable=True),
        sa.Column('oauth_redirect_path', sa.String(length=200), nullable=True),
        sa.Column('oauth_client_id_env', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )


def downgrade() -> None:
    op.drop_table('tool_marketplace')
