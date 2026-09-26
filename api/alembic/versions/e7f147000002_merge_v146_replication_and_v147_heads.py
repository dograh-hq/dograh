"""merge retained v1.46 replication and v1.47 migration heads

Revision ID: e7f147000002
Revises: e7f147000001, d7b4c1a2e9f0
Create Date: 2026-09-26 00:00:01.000000

The v1.46 Sakinah release adds durable artifact-replication state on a
separate history branch.  Joining it with the existing v1.47/Sakinah merge
ensures an upgrade applies both lines without an operator-selected stamp.
"""

from typing import Sequence, Union


revision: str = "e7f147000002"
down_revision: Union[str, Sequence[str], None] = (
    "e7f147000001",
    "d7b4c1a2e9f0",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join the v1.46 replication and v1.47 migration histories."""


def downgrade() -> None:
    """Split history only; each parent retains its own downgrade."""
