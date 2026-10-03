"""Tests for Knowledge Base HNSW vector similarity index (Issue #635)."""

import importlib

from sqlalchemy import Index

from api.db.models import KnowledgeBaseChunkModel


def test_kb_chunk_model_uses_hnsw_index():
    """Verify that KnowledgeBaseChunkModel declares an HNSW index rather than IVFFlat."""
    indexes = [
        arg for arg in KnowledgeBaseChunkModel.__table_args__ if isinstance(arg, Index)
    ]
    index_names = {idx.name: idx for idx in indexes}

    # Verify IVFFlat index is removed
    assert "ix_kb_chunks_embedding_ivfflat" not in index_names, (
        "Legacy IVFFlat index should be replaced by HNSW"
    )

    # Verify HNSW index is present with correct dialect options
    assert "ix_kb_chunks_embedding_hnsw" in index_names, (
        "HNSW index ix_kb_chunks_embedding_hnsw must be defined on KnowledgeBaseChunkModel"
    )

    hnsw_index = index_names["ix_kb_chunks_embedding_hnsw"]
    assert hnsw_index.dialect_options["postgresql"]["using"] == "hnsw"
    assert hnsw_index.dialect_options["postgresql"]["ops"] == {
        "embedding": "vector_cosine_ops"
    }
    assert [col.name for col in hnsw_index.columns] == ["embedding"]


def test_kb_chunk_hnsw_migration_metadata():
    """Verify the migration script metadata and revision chain."""
    migration = importlib.import_module(
        "api.alembic.versions.a1c9e8f4b3d7_migrate_kb_chunks_vector_index_to_hnsw"
    )

    assert migration.revision == "a1c9e8f4b3d7"
    assert migration.down_revision == "f3a1c47b9e02"
    assert hasattr(migration, "upgrade") and callable(migration.upgrade)
    assert hasattr(migration, "downgrade") and callable(migration.downgrade)
