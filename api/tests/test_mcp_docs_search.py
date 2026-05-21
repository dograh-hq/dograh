"""Unit tests for the MCP docs discovery tools."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.mcp_server.tools import docs_search as docs_search_module
from api.mcp_server.tools.docs_search import (
    _docs_index,
    _extract_page_title,
    _resolve_docs_root,
    _score_page,
    _strip_frontmatter,
    _tokenize_query,
    list_docs,
    read_doc,
    search_docs,
)


def _clear_docs_caches() -> None:
    docs_search_module._docs_index.cache_clear()


@pytest.fixture
def fake_docs_root(tmp_path: Path) -> Path:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()

    (docs_root / "getting-started").mkdir()
    (docs_root / "getting-started" / "index.mdx").write_text(
        "---\n"
        'title: "Getting started"\n'
        'description: "Start using Dograh."\n'
        "---\n\n"
        "# Getting started\n\n"
        "Welcome to Dograh.\n",
        encoding="utf-8",
    )

    (docs_root / "voice-agent").mkdir()
    (docs_root / "voice-agent" / "introduction.mdx").write_text(
        "---\n"
        'title: "Voice Agent Builder"\n'
        'description: "Build conversational workflows."\n'
        "---\n\n"
        "# Voice Agent Builder\n\n"
        "Build workflows with nodes and tools.\n",
        encoding="utf-8",
    )

    (docs_root / "voice-agent" / "tools").mkdir()
    (docs_root / "voice-agent" / "tools" / "mcp-tool.mdx").write_text(
        "---\n"
        'title: "MCP Tool"\n'
        'description: "Connect external MCP servers."\n'
        'llm_hint: "Use for MCP server setup, remote tools, or model context protocol questions."\n'
        "aliases:\n"
        '  - "model context protocol"\n'
        "---\n\n"
        "# MCP Tool\n\n"
        "Connect an external MCP server to your voice agent.\n\n"
        "## Authentication\n\n"
        "Provide the MCP endpoint URL and headers.\n",
        encoding="utf-8",
    )

    (docs_root / "deployment").mkdir()
    (docs_root / "deployment" / "docker.mdx").write_text(
        "---\n"
        'title: "Docker"\n'
        'description: "Deploy Dograh with Docker."\n'
        'llm_hint: "Use for Docker deployment, local setup, remote setup, TURN server, coturn, or WebRTC connectivity questions."\n'
        "aliases:\n"
        '  - "coturn"\n'
        '  - "turn server"\n'
        "---\n\n"
        "# Docker\n\n"
        "Run Dograh with Docker.\n\n"
        "## Troubleshooting WebRTC Connectivity\n\n"
        "If audio fails or ICE fails, configure a TURN server. Coturn is the recommended choice.\n",
        encoding="utf-8",
    )

    # Hidden/orphaned docs page: present on disk but not in docs.json, so it
    # must not be indexed by the MCP tools.
    (docs_root / "internal-only.mdx").write_text(
        "---\n"
        'title: "Internal TURN Notes"\n'
        "---\n\n"
        "# Internal TURN Notes\n\n"
        "This page mentions zyxinternalturntoken but is not user-facing.\n",
        encoding="utf-8",
    )

    (docs_root / "AGENTS.md").write_text("# Internal instructions\n", encoding="utf-8")

    (docs_root / "docs.json").write_text(
        """{
  "navigation": {
    "tabs": [
      {
        "tab": "Guides",
        "groups": [
          {
            "group": "Getting started",
            "pages": [
              "getting-started/index"
            ]
          },
          {
            "group": "Voice Agent Builder",
            "pages": [
              "voice-agent/introduction",
              {
                "group": "Tools",
                "pages": [
                  "voice-agent/tools/mcp-tool"
                ]
              }
            ]
          }
        ]
      },
      {
        "tab": "Developer",
        "groups": [
          {
            "group": "Deployment",
            "pages": [
              "deployment/docker"
            ]
          }
        ]
      }
    ]
  }
}
""",
        encoding="utf-8",
    )

    _clear_docs_caches()
    with patch.dict(os.environ, {"DOGRAH_DOCS_PATH": str(docs_root)}):
        yield docs_root
    _clear_docs_caches()


@pytest.fixture
def authed_user():
    class _FakeUser:
        selected_organization_id = 1
        id = 42

    with patch(
        "api.mcp_server.tools.docs_search.authenticate_mcp_request",
        new=AsyncMock(return_value=_FakeUser()),
    ):
        yield _FakeUser()


def test_tokenize_query_dedupes_and_drops_stopwords():
    assert _tokenize_query("How do I configure a TURN server TURN?") == [
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


def test_extract_page_title_prefers_frontmatter():
    body = '---\ntitle: "Front Title"\n---\n\n# Heading Title\n'
    assert _extract_page_title(body, fallback="x.mdx") == "Front Title"


def test_extract_page_title_falls_back_to_first_heading():
    body = "# Heading Title\nbody\n"
    assert _extract_page_title(body, fallback="x.mdx") == "Heading Title"


def test_score_page_uses_llm_hint_and_aliases():
    page = docs_search_module.DocPage(
        path="deployment/docker",
        file_path="deployment/docker.mdx",
        title="Docker",
        description="Deploy Dograh with Docker.",
        llm_hint="Use for TURN server and coturn setup.",
        aliases=("coturn",),
        breadcrumb=("Developer", "Deployment"),
        content="Docker deployment.",
        sections=(
            docs_search_module.DocSection(
                title="Troubleshooting WebRTC Connectivity",
                slug="troubleshooting-webrtc-connectivity",
                level=2,
                content="Configure a TURN server with coturn.",
            ),
        ),
        order=0,
    )
    score, section = _score_page(page, ["coturn"])
    assert score > 0
    assert section is not None
    assert section.slug == "troubleshooting-webrtc-connectivity"


def test_resolve_docs_root_honors_env_override(tmp_path: Path):
    docs = tmp_path / "custom_docs"
    docs.mkdir()
    (docs / "docs.json").write_text("{}", encoding="utf-8")
    with patch.dict(os.environ, {"DOGRAH_DOCS_PATH": str(docs)}):
        assert _resolve_docs_root() == docs.resolve()


@pytest.mark.asyncio
async def test_search_docs_ranks_turn_doc_and_uses_route_path(
    fake_docs_root, authed_user
):
    results = await search_docs("How do I configure coturn for WebRTC?")
    assert results
    assert results[0]["path"] == "deployment/docker"
    assert results[0]["section_slug"] == "troubleshooting-webrtc-connectivity"
    assert "TURN server" in results[0]["llm_hint"]
    assert "snippet" not in results[0]
    assert "score" not in results[0]
    assert "url" not in results[0]


@pytest.mark.asyncio
async def test_search_docs_indexes_only_docs_json_pages(fake_docs_root, authed_user):
    results = await search_docs("zyxinternalturntoken")
    assert results == []


@pytest.mark.asyncio
async def test_search_docs_respects_limit(fake_docs_root, authed_user):
    results = await search_docs("dograh", limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_docs_returns_empty_when_no_match(fake_docs_root, authed_user):
    assert await search_docs("xyzzy unrelated zzz") == []


@pytest.mark.asyncio
async def test_search_docs_returns_empty_when_no_corpus(
    tmp_path, authed_user, monkeypatch
):
    nonexistent = tmp_path / "no-docs-here"
    monkeypatch.setenv("DOGRAH_DOCS_PATH", str(nonexistent))
    _clear_docs_caches()
    with patch(
        "api.mcp_server.tools.docs_search._resolve_docs_root", return_value=None
    ):
        assert await search_docs("anything") == []


@pytest.mark.asyncio
async def test_search_docs_rejects_empty_query(fake_docs_root, authed_user):
    with pytest.raises(ValueError, match="non-empty string"):
        await search_docs("")


@pytest.mark.asyncio
async def test_search_docs_rejects_query_with_only_stopwords(
    fake_docs_root, authed_user
):
    with pytest.raises(ValueError, match="non-stopword"):
        await search_docs("how do I")


@pytest.mark.asyncio
async def test_search_docs_rejects_zero_limit(fake_docs_root, authed_user):
    with pytest.raises(ValueError, match="at least 1"):
        await search_docs("Dograh", limit=0)


@pytest.mark.asyncio
async def test_list_docs_returns_top_level_sections(fake_docs_root, authed_user):
    results = await list_docs()
    assert results[0]["kind"] == "section"
    assert results[0]["path"] == "guides/getting-started"
    assert results[1]["path"] == "guides/voice-agent-builder"


@pytest.mark.asyncio
async def test_list_docs_depth_expands_children(fake_docs_root, authed_user):
    results = await list_docs("guides/voice-agent-builder", depth=2)
    paths = [item["path"] for item in results]
    assert "voice-agent/introduction" in paths
    assert "guides/voice-agent-builder/tools" in paths
    assert "voice-agent/tools/mcp-tool" in paths


@pytest.mark.asyncio
async def test_list_docs_rejects_unknown_section(fake_docs_root, authed_user):
    with pytest.raises(HTTPException, match="Unknown docs section"):
        await list_docs("nope")


@pytest.mark.asyncio
async def test_read_doc_returns_full_page_and_sections(fake_docs_root, authed_user):
    result = await read_doc("deployment/docker")
    assert result["path"] == "deployment/docker"
    assert result["title"] == "Docker"
    assert "url" not in result
    section_slugs = [section["slug"] for section in result["sections"]]
    assert "docker" in section_slugs
    assert "troubleshooting-webrtc-connectivity" in section_slugs
    assert "Coturn" in result["content"] or "coturn" in result["content"].lower()


@pytest.mark.asyncio
async def test_read_doc_can_target_section(fake_docs_root, authed_user):
    result = await read_doc(
        "deployment/docker",
        section="troubleshooting-webrtc-connectivity",
    )
    assert result["section_slug"] == "troubleshooting-webrtc-connectivity"
    assert "ICE fails" in result["content"] or "TURN server" in result["content"]
    assert "Run Dograh with Docker." not in result["content"]


@pytest.mark.asyncio
async def test_read_doc_rejects_unknown_page(fake_docs_root, authed_user):
    with pytest.raises(HTTPException, match="Unknown docs page"):
        await read_doc("missing/page")


@pytest.mark.asyncio
async def test_read_doc_rejects_unknown_section(fake_docs_root, authed_user):
    with pytest.raises(HTTPException, match="Unknown section"):
        await read_doc("deployment/docker", section="missing-section")


def test_docs_index_uses_docs_json_navigation(fake_docs_root):
    index = _docs_index()
    assert "internal-only" not in index.pages_by_path
    assert "guides/voice-agent-builder/tools" in index.sections_by_path
    assert index.pages_by_path["voice-agent/tools/mcp-tool"].breadcrumb == (
        "Guides",
        "Voice Agent Builder",
        "Tools",
    )
