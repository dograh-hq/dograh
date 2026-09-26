"""merge upstream and CALMOS white-label migration heads

Revision ID: b9e0c3a5d7f1
Revises: 4f1c2d3e5a67, f3a1c47b9e02
Create Date: 2026-09-05 13:58:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "b9e0c3a5d7f1"
down_revision: Union[str, Sequence[str], None] = ("4f1c2d3e5a67", "f3a1c47b9e02")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
