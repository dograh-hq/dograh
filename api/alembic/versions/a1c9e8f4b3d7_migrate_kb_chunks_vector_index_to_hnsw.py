"""migrate kb chunks vector index to hnsw

Revision ID: a1c9e8f4b3d7
Revises: f3a1c47b9e02
Create Date: 2026-09-07 19:51:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c9e8f4b3d7"
down_revision: Union[str, None] = "f3a1c47b9e02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the legacy IVFFlat index on knowledge_base_chunks.embedding.
    # IVFFlat computes centroids at index build time (which was on an empty table
    # during initial migration), leading to severely degraded recall on small-to-medium
    # document collections with pgvector's default probes=1.
    op.drop_index(
        "ix_kb_chunks_embedding_ivfflat",
        table_name="knowledge_base_chunks",
        postgresql_using="ivfflat",
    )

    # Create HNSW index for vector cosine similarity search.
    # HNSW builds an incremental graph without a pre-clustering training step,
    # avoiding the empty-table centroid problem and delivering high recall out-of-the-box.
    op.create_index(
        "ix_kb_chunks_embedding_hnsw",
        "knowledge_base_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kb_chunks_embedding_hnsw",
        table_name="knowledge_base_chunks",
        postgresql_using="hnsw",
    )
    op.create_index(
        "ix_kb_chunks_embedding_ivfflat",
        "knowledge_base_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
