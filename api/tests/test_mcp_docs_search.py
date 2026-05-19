"""Unit tests for the `search_docs` MCP tool.

The tool reads the docs corpus from disk via ``_resolve_docs_root`` and
caches it with ``functools.lru_cache``. These tests point the cache at
a synthetic corpus per-test so the assertions don't depend on the real
docs tree (which evolves) and the LRU cache doesn't leak state.

`authenticate_mcp_request` is mocked so the tests don't need a live DB
or a valid API key — mirroring the pattern in
``test_mcp_save_workflow.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from api.mcp_server.tools import docs_search as docs_search_module
from api.mcp_server.tools.docs_search import (
    _docs_url_for,
    _extract_page_title,
    _resolve_docs_root,
    _score_page,
    _strip_frontmatter,
    _tokenize_query,
    search_docs,
)


# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_docs_root(tmp_path: Path) -> Path:
    """Build a minimal docs tree on disk and point the tool at it."""
    docs_root = tmp_path / "docs"
    docs_root.mkdir()

    (docs_root / "configurations").mkdir()
    (docs_root / "configurations" / "voice.mdx").write_text(
        "---\n"
        'title: "Voice"\n'
        "---\n\n"
        "# Voice configuration\n\n"
        "Dograh supports ElevenLabs and Cartesia TTS providers.\n"
        "Configure the ElevenLabs voice_id in your workspace settings.\n",
        encoding="utf-8",
    )
    (docs_root / "configurations" / "transcriber.mdx").write_text(
        "---\n"
        'title: "Transcriber"\n'
        "---\n\n"
        "# Speech-to-text\n\nDeepgram is the default transcriber.\n",
        encoding="utf-8",
    )

    (docs_root / "deployment").mkdir()
    (docs_root / "deployment" / "turn-server.mdx").write_text(
        "---\n"
        'title: "TURN server setup"\n'
        "---\n\n"
        "# TURN server\n\n"
        "WebRTC requires a TURN server for NAT traversal. Coturn is the "
        "recommended choice for self-hosted deployments.\n",
        encoding="utf-8",
    )

    # A non-doc file that must be ignored by the corpus loader.
    (docs_root / "docs.json").write_text('{"name":"Dograh"}', encoding="utf-8")

    # Reset the LRU cache and pin the resolver to our tmp tree.
    docs_search_module._docs_corpus.cache_clear()
    with patch.dict(os.environ, {"DOGRAH_DOCS_PATH": str(docs_root)}):
        yield docs_root
    docs_search_module._docs_corpus.cache_clear()


@pytest.fixture
def authed_user():
    """Stub ``authenticate_mcp_request`` so tests skip the API-key path."""

    class _FakeUser:
        selected_organization_id = 1
        id = 42

    with patch(
        "api.mcp_server.tools.docs_search.authenticate_mcp_request",
        new=AsyncMock(return_value=_FakeUser()),
    ):
        yield _FakeUser()


# ─── Pure helpers ────────────────────────────────────────────────────────


def test_tokenize_query_strips_short_and_punct_terms():
    """Punctuation and 1-char tokens must not bleed into the scorer.

    A trailing `?` or stray `a` would otherwise match nearly every page
    and flatten the relevance ranking.
    """
    assert _tokenize_query("How do I configure a TURN server?") == [
        "how",
        "do",
        "configure",
        "turn",
        "server",
    ]


def test_tokenize_query_empty_input_returns_empty():
    assert _tokenize_query("") == []
    assert _tokenize_query("?? // !!") == []


def test_strip_frontmatter_removes_yaml_block():
    body = '---\ntitle: "X"\n---\n\n# Heading\n'
    assert _strip_frontmatter(body).startswith("# Heading")


def test_strip_frontmatter_passes_through_when_missing():
    body = "# Just a heading\nbody text\n"
    assert _strip_frontmatter(body) == body


def test_extract_page_title_prefers_frontmatter():
    body = '---\ntitle: "Front Title"\n---\n\n# Heading Title\n'
    assert _extract_page_title(body, fallback="x.mdx") == "Front Title"


def test_extract_page_title_falls_back_to_first_heading():
    """When frontmatter is missing the first ATX heading is the next best
    signal — better than just returning the filename, which often is
    a slug not a human-readable title."""
    body = "# Heading Title\nbody\n"
    assert _extract_page_title(body, fallback="x.mdx") == "Heading Title"


def test_extract_page_title_falls_back_to_filename_when_nothing_matches():
    body = "plain prose with no heading or frontmatter"
    assert _extract_page_title(body, fallback="x.mdx") == "x.mdx"


def test_docs_url_for_strips_extension_and_index():
    assert (
        _docs_url_for("configurations/voice.mdx")
        == "https://docs.dograh.com/configurations/voice"
    )
    assert (
        _docs_url_for("getting-started/index.mdx")
        == "https://docs.dograh.com/getting-started"
    )


def test_score_page_weights_title_above_body():
    """Title hits must outweigh body hits — otherwise a long page that
    incidentally mentions the term many times outranks the page whose
    purpose IS the term."""
    title_only = _score_page(
        rel_path="other.mdx", title="TURN server", body="unrelated text", terms=["turn"]
    )
    body_only = _score_page(
        rel_path="other.mdx",
        title="Unrelated",
        body="turn turn turn turn turn",
        terms=["turn"],
    )
    assert title_only > body_only


def test_score_page_returns_zero_when_no_terms_match():
    assert (
        _score_page(
            rel_path="x.mdx", title="X", body="hello world", terms=["nonexistent"]
        )
        == 0
    )


def test_resolve_docs_root_honors_env_override(tmp_path: Path):
    docs = tmp_path / "custom_docs"
    docs.mkdir()
    with patch.dict(os.environ, {"DOGRAH_DOCS_PATH": str(docs)}):
        assert _resolve_docs_root() == docs.resolve()


def test_resolve_docs_root_ignores_nonexistent_env_value(tmp_path: Path):
    """A bogus env value must not crash the tool — fall back to discovery
    (the real ``docs/`` in the repo) instead."""
    with patch.dict(os.environ, {"DOGRAH_DOCS_PATH": str(tmp_path / "nope")}):
        # Walk-up discovery should land somewhere (the repo's actual docs)
        # but we don't assert the exact path because it depends on where
        # the tests are run; we just assert no crash and either None or a dir.
        resolved = _resolve_docs_root()
        assert resolved is None or resolved.is_dir()


# ─── End-to-end tool behaviour ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_docs_ranks_turn_setup_first_for_turn_query(
    fake_docs_root, authed_user
):
    """The page whose title and body are both about TURN must outrank
    incidental mentions of related words on other pages."""
    results = await search_docs("How do I set up a TURN server?")
    assert results, "expected at least one result"
    assert results[0]["path"] == "deployment/turn-server.mdx"
    assert results[0]["url"] == "https://docs.dograh.com/deployment/turn-server"
    assert "TURN server" in results[0]["title"]
    assert "TURN" in results[0]["snippet"] or "turn" in results[0]["snippet"].lower()


@pytest.mark.asyncio
async def test_search_docs_excludes_non_doc_files(fake_docs_root, authed_user):
    """``docs.json`` must not appear — the corpus loader filters to
    .mdx/.md only."""
    results = await search_docs("Dograh")
    paths = [r["path"] for r in results]
    assert "docs.json" not in paths


@pytest.mark.asyncio
async def test_search_docs_returns_empty_when_no_match(fake_docs_root, authed_user):
    results = await search_docs("xyzzy unrelated zzz")
    assert results == []


@pytest.mark.asyncio
async def test_search_docs_respects_limit(fake_docs_root, authed_user):
    """``limit=1`` must collapse the result list even if multiple pages
    match."""
    results = await search_docs("Dograh", limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_docs_clamps_limit_to_hard_cap(fake_docs_root, authed_user):
    """A pathological large limit must be clamped to
    ``DOCS_SEARCH_MAX_LIMIT`` (=25) so the payload stays bounded."""
    # Drop in extra docs so there's headroom to verify the clamp.
    for i in range(30):
        (fake_docs_root / f"extra-{i}.mdx").write_text(
            f"# Page {i}\nThis Dograh page covers configurations topic {i}.\n",
            encoding="utf-8",
        )
    docs_search_module._docs_corpus.cache_clear()
    results = await search_docs("Dograh", limit=999)
    assert len(results) <= 25


@pytest.mark.asyncio
async def test_search_docs_returns_empty_when_no_corpus(
    tmp_path, authed_user, monkeypatch
):
    """If the docs directory doesn't exist on disk, the tool must
    degrade to an empty list rather than raising — Docker images and
    dev checkouts can disagree on layout."""
    nonexistent = tmp_path / "no-docs-here"
    monkeypatch.setenv("DOGRAH_DOCS_PATH", str(nonexistent))
    # Also block the walk-up fallback by pointing the resolver at a
    # tmp path with no `docs/` ancestor.
    docs_search_module._docs_corpus.cache_clear()
    with patch(
        "api.mcp_server.tools.docs_search._resolve_docs_root", return_value=None
    ):
        results = await search_docs("anything")
    assert results == []


@pytest.mark.asyncio
async def test_search_docs_rejects_empty_query(fake_docs_root, authed_user):
    with pytest.raises(ValueError, match="non-empty string"):
        await search_docs("")


@pytest.mark.asyncio
async def test_search_docs_rejects_query_with_no_real_terms(
    fake_docs_root, authed_user
):
    """A query like `"???"` tokenizes to nothing — surface an actionable
    error rather than silently returning every page."""
    with pytest.raises(ValueError, match="2\\+ alphanumeric"):
        await search_docs("?? // !!")


@pytest.mark.asyncio
async def test_search_docs_rejects_zero_limit(fake_docs_root, authed_user):
    with pytest.raises(ValueError, match="at least 1"):
        await search_docs("Dograh", limit=0)
