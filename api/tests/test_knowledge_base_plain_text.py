"""Local plain-text (.txt) knowledge base ingestion.

Regression coverage for #634: .txt routed through MPS came back with Japanese
characters dropped or altered. Stored text must now match the upload exactly.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from api.tasks import knowledge_base_processing as kb
from api.tasks.knowledge_base_processing import (
    _estimate_size,
    _read_plain_text,
    _split_plain_text,
    process_knowledge_base_document,
)

JAPANESE_SAMPLE = (
    "ダウンロードガイダンス：スポーツプログラムのデータベースをご確認ください。"
    "パソコンで全部の資料をダウンロードできます。"
    "詳しくは案内係までお問い合わせください。電話番号は03-1234-5678です。"
)


def test_japanese_sample_survives_chunking_unchanged():
    """Issue #634: no character may be dropped, altered or inserted."""
    chunks = _split_plain_text(JAPANESE_SAMPLE, 128)

    assert "".join(chunks) == JAPANESE_SAMPLE

    # #634 stored "タウンロートカイタンス ： スホーツフロクラム ..." instead:
    # dakuten stripped, kanji dropped, spaces inserted between characters.
    rejoined = "".join(chunks)
    assert "ダウンロード" in rejoined
    assert "スポーツ" in rejoined
    assert "確認" in rejoined and "電話番号" in rejoined
    assert "タウンロート" not in rejoined
    assert " " not in rejoined


def test_long_japanese_document_reassembles_exactly():
    text = "\n".join([JAPANESE_SAMPLE] * 200)

    chunks = _split_plain_text(text, 128)

    assert "".join(chunks) == text
    assert len(chunks) > 1
    assert all(chunk for chunk in chunks)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "single line",
        "trailing newline\n",
        "\n\n\n",
        "windows\r\nline\r\nendings\r\n",
        "mixed ASCII and 日本語 in one paragraph\nsecond line\n",
        JAPANESE_SAMPLE * 20,
        "x" * 5000,
        "あ" * 5000,
    ],
)
def test_split_is_lossless(text):
    assert "".join(_split_plain_text(text, 128)) == text


def test_empty_text_produces_no_chunks():
    assert _split_plain_text("", 128) == []


def test_single_line_longer_than_budget_is_hard_split():
    text = "あ" * 500

    chunks = _split_plain_text(text, 128)

    assert "".join(chunks) == text
    assert len(chunks) > 1


def test_chunks_respect_the_budget_within_one_unit():
    text = "\n".join([JAPANESE_SAMPLE] * 50)

    chunks = _split_plain_text(text, 128)

    assert all(_estimate_size(chunk) <= 128 * 2 for chunk in chunks)


def test_tiny_budget_still_lossless():
    assert "".join(_split_plain_text(JAPANESE_SAMPLE, 1)) == JAPANESE_SAMPLE


def test_estimate_size_is_an_approximation_not_a_tokenizer():
    assert _estimate_size("abcd") == 1
    assert _estimate_size("あいうえお") == 5
    assert _estimate_size("abcd" + "あい") == 3
    assert _estimate_size("") == 0


def test_reads_utf8_exactly(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text(JAPANESE_SAMPLE, encoding="utf-8")

    assert _read_plain_text(str(path)) == JAPANESE_SAMPLE


def test_leading_bom_is_consumed_but_inner_feff_is_preserved(tmp_path):
    path = tmp_path / "bom.txt"
    path.write_text(JAPANESE_SAMPLE, encoding="utf-8-sig")

    assert _read_plain_text(str(path)) == JAPANESE_SAMPLE

    # a U+FEFF that is not the leading BOM is ordinary content
    inner = tmp_path / "inner.txt"
    inner.write_text("first﻿second", encoding="utf-8")
    assert _read_plain_text(str(inner)) == "first﻿second"


def test_crlf_line_endings_are_preserved(tmp_path):
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"line one\r\nline two\r\n")

    assert _read_plain_text(str(path)) == "line one\r\nline two\r\n"


def test_invalid_utf8_raises_rather_than_substituting(tmp_path):
    path = tmp_path / "shift_jis.txt"
    path.write_bytes(JAPANESE_SAMPLE.encode("shift_jis"))

    with pytest.raises(UnicodeDecodeError):
        _read_plain_text(str(path))


class _FakeDocument:
    id = 1
    organization_id = 7
    document_uuid = "doc-uuid"
    filename = "repro_sample.txt"
    created_by = 3


class _FakeEmbeddingService:
    def get_model_id(self):
        return "text-embedding-3-small"

    def get_embedding_dimension(self):
        return 1536

    async def embed_texts(self, texts):
        return [[0.0] * 1536 for _ in texts]


@pytest.fixture
def kb_task_env(monkeypatch, tmp_path):
    """Stub storage, DB and MPS so the task runs without external services."""

    state = {
        "file_bytes": JAPANESE_SAMPLE.encode("utf-8"),
        "status": [],
        "chunks": [],
        "full_text": None,
    }

    async def fake_download(s3_key, local_path):
        Path(local_path).write_bytes(state["file_bytes"])
        return True

    async def fake_update_status(document_id, status, **kwargs):
        state["status"].append((status, kwargs))

    async def fake_replace_chunks(document_id, organization_id, chunks):
        state["chunks"] = chunks
        return chunks

    async def fake_update_full_text(document_id, full_text):
        state["full_text"] = full_text

    monkeypatch.setattr(kb.storage_fs, "adownload_file", fake_download)
    monkeypatch.setattr(kb.db_client, "update_document_status", fake_update_status)
    monkeypatch.setattr(kb.db_client, "update_document_metadata", AsyncMock())
    monkeypatch.setattr(
        kb.db_client, "get_document_by_id", AsyncMock(return_value=_FakeDocument())
    )
    monkeypatch.setattr(
        kb.db_client, "get_document_by_hash", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        kb.db_client, "replace_chunks_for_document", fake_replace_chunks
    )
    monkeypatch.setattr(
        kb.db_client, "update_document_full_text", fake_update_full_text
    )

    process_document = AsyncMock(
        return_value={
            "mode": "chunked",
            "docling_metadata": {},
            "full_text": "mps text",
            "chunks": [{"chunk_text": "mps text", "chunk_index": 0, "token_count": 2}],
        }
    )
    monkeypatch.setattr(kb.mps_service_key_client, "process_document", process_document)
    state["process_document"] = process_document

    async def fake_build_embedding_service(**kwargs):
        return _FakeEmbeddingService()

    monkeypatch.setattr(kb, "build_embedding_service", fake_build_embedding_service)

    async def fake_resolved_config(organization_id):
        class _Embeddings:
            provider = "openai"
            api_key = "sk-test"
            model = "text-embedding-3-small"
            base_url = None
            endpoint = None
            api_version = None

        class _Effective:
            embeddings = _Embeddings()

        class _Resolved:
            effective = _Effective()

        return _Resolved()

    import api.services.configuration.ai_model_configuration as ai_config

    monkeypatch.setattr(
        ai_config, "get_resolved_ai_model_configuration", fake_resolved_config
    )
    monkeypatch.setattr(
        ai_config, "apply_managed_embeddings_base_url", lambda provider, base_url: None
    )

    return state


async def test_txt_ingestion_bypasses_mps_and_preserves_japanese(kb_task_env):
    await process_knowledge_base_document(
        ctx={},
        document_id=1,
        s3_key="knowledge_base/7/uuid/repro_sample.txt",
        organization_id=7,
        created_by_provider_id="provider-1",
    )

    kb_task_env["process_document"].assert_not_awaited()

    stored = "".join(chunk.chunk_text for chunk in kb_task_env["chunks"])
    assert stored == JAPANESE_SAMPLE
    assert kb_task_env["status"][-1][0] == "completed"


async def test_txt_full_document_mode_stores_exact_text(kb_task_env):
    await process_knowledge_base_document(
        ctx={},
        document_id=1,
        s3_key="knowledge_base/7/uuid/repro_sample.txt",
        organization_id=7,
        created_by_provider_id="provider-1",
        retrieval_mode="full_document",
    )

    kb_task_env["process_document"].assert_not_awaited()
    assert kb_task_env["full_text"] == JAPANESE_SAMPLE
    assert kb_task_env["status"][-1][0] == "completed"


async def test_non_plain_text_still_goes_through_mps(kb_task_env):
    kb_task_env["file_bytes"] = b"%PDF-1.4 fake pdf bytes"

    await process_knowledge_base_document(
        ctx={},
        document_id=1,
        s3_key="knowledge_base/7/uuid/report.pdf",
        organization_id=7,
        created_by_provider_id="provider-1",
    )

    kb_task_env["process_document"].assert_awaited_once()
    assert [chunk.chunk_text for chunk in kb_task_env["chunks"]] == ["mps text"]


async def test_invalid_utf8_txt_fails_the_document_without_calling_mps(kb_task_env):
    kb_task_env["file_bytes"] = JAPANESE_SAMPLE.encode("shift_jis")

    await process_knowledge_base_document(
        ctx={},
        document_id=1,
        s3_key="knowledge_base/7/uuid/shift_jis.txt",
        organization_id=7,
        created_by_provider_id="provider-1",
    )

    kb_task_env["process_document"].assert_not_awaited()
    status, kwargs = kb_task_env["status"][-1]
    assert status == "failed"
    assert "not valid UTF-8" in kwargs["error_message"]
    assert kb_task_env["chunks"] == []
