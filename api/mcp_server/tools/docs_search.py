"""MCP docs discovery tools over the Mintlify docs tree.

The docs surface is intentionally split into three steps:

- ``list_docs`` for lightweight navigation over the published hierarchy
- ``search_docs`` for keyword lookup across the visible docs catalog
- ``read_doc`` for the full content of one chosen page (or one section)

The runtime index is derived from ``docs/docs.json`` plus the referenced
``.mdx``/``.md`` files. That keeps navigation, ordering, and visibility in
sync with the published docs rather than indexing every file under ``docs/``.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool

DOCS_SEARCH_MAX_LIMIT = 25
DOCS_LIST_MAX_DEPTH = 3
_ROOT_SECTION_PATH = "__root__"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "by",
    "can",
    "do",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "with",
    "you",
    "your",
}


@dataclass(frozen=True)
class DocSection:
    title: str
    slug: str
    level: int
    content: str


@dataclass(frozen=True)
class DocPage:
    path: str
    file_path: str
    title: str
    description: str
    llm_hint: str
    aliases: tuple[str, ...]
    breadcrumb: tuple[str, ...]
    content: str
    sections: tuple[DocSection, ...]
    order: int

    def breadcrumb_text(self) -> str:
        return " > ".join(self.breadcrumb)

    def routing_hint(self) -> str:
        return self.llm_hint or self.description

    def to_catalog_dict(self, section: DocSection | None = None) -> dict:
        data = {
            "kind": "page",
            "path": self.path,
            "title": self.title,
            "breadcrumb": self.breadcrumb_text(),
            "llm_hint": self.routing_hint(),
        }
        if section is not None:
            data["section_title"] = section.title
            data["section_slug"] = section.slug
        return _compact_dict(data)

    def to_read_dict(self, section: DocSection | None = None) -> dict:
        active_section = section
        content = self.content
        if active_section is not None:
            content = active_section.content

        return _compact_dict(
            {
                "path": self.path,
                "title": self.title,
                "breadcrumb": self.breadcrumb_text(),
                "llm_hint": self.routing_hint(),
                "section_title": active_section.title if active_section else None,
                "section_slug": active_section.slug if active_section else None,
                "content": content,
                "sections": [
                    {"title": sec.title, "slug": sec.slug}
                    for sec in self.sections
                    if sec.title and sec.slug
                ],
            }
        )


@dataclass(frozen=True)
class NavSection:
    path: str
    title: str
    breadcrumb: tuple[str, ...]
    children: tuple[tuple[str, str], ...]
    descendant_page_count: int = 0

    def breadcrumb_text(self) -> str:
        return " > ".join(self.breadcrumb)

    def to_mcp_dict(self) -> dict:
        hint = None
        if self.descendant_page_count:
            hint = f"Browse {self.descendant_page_count} docs in this section."
        return _compact_dict(
            {
                "kind": "section",
                "path": self.path,
                "title": self.title,
                "breadcrumb": self.breadcrumb_text(),
                "llm_hint": hint,
                "has_children": bool(self.children),
                "child_count": len(self.children),
                "page_count": self.descendant_page_count,
            }
        )


@dataclass(frozen=True)
class DocsIndex:
    pages_by_path: dict[str, DocPage]
    sections_by_path: dict[str, NavSection]


def _compact_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in data.items() if value not in (None, "", [], (), {})
    }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


def _coerce_docs_root(candidate: Path) -> Path | None:
    candidate = candidate.expanduser().resolve()
    if (candidate / "docs.json").is_file():
        return candidate
    nested = candidate / "docs"
    if (nested / "docs.json").is_file():
        return nested
    return None


def _resolve_docs_root() -> Path | None:
    """Return the path to the on-disk docs tree, or None if not found."""
    override = os.environ.get("DOGRAH_DOCS_PATH")
    if override:
        resolved = _coerce_docs_root(Path(override))
        if resolved is not None:
            return resolved

    docker_default = _coerce_docs_root(Path("/app/docs"))
    if docker_default is not None:
        return docker_default

    for parent in Path(__file__).resolve().parents:
        resolved = _coerce_docs_root(parent / "docs")
        if resolved is not None:
            return resolved

    return None


def _split_frontmatter(contents: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(contents)
    if not match:
        return {}, contents
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, contents
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return frontmatter, contents[match.end() :].lstrip("\n")


def _strip_frontmatter(contents: str) -> str:
    """Drop the YAML frontmatter block from a docs page body."""
    return _split_frontmatter(contents)[1]


def _clean_heading_text(raw: str) -> str:
    text = re.sub(r"\s*\{#.*\}\s*$", "", raw.strip())
    return " ".join(text.split())


def _extract_page_title(contents: str, fallback: str) -> str:
    """Pull a human-readable title for a docs page."""
    frontmatter, body = _split_frontmatter(contents)
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    match = _HEADING_RE.search(body)
    if match:
        return _clean_heading_text(match.group(2))

    return fallback


def _normalize_text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.strip().split())
    return ""


def _normalize_aliases(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        aliases = [value]
    elif isinstance(value, list):
        aliases = [item for item in value if isinstance(item, str)]
    else:
        aliases = []
    return tuple(alias.strip() for alias in aliases if alias.strip())


def _extract_sections(body: str) -> tuple[DocSection, ...]:
    matches = list(_HEADING_RE.finditer(body))
    stripped_body = body.strip()
    if not matches:
        if not stripped_body:
            return ()
        return (
            DocSection(
                title="Overview",
                slug="overview",
                level=1,
                content=stripped_body,
            ),
        )

    sections: list[DocSection] = []
    preamble = body[: matches[0].start()].strip()
    if preamble:
        sections.append(
            DocSection(
                title="Overview",
                slug="overview",
                level=1,
                content=preamble,
            )
        )

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        title = _clean_heading_text(match.group(2))
        sections.append(
            DocSection(
                title=title or "Section",
                slug=_slugify(title or "section"),
                level=len(match.group(1)),
                content=body[start:end].strip(),
            )
        )
    return tuple(sections)


def _tokenize_text(text: str) -> list[str]:
    return [
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= 2 and token not in _STOPWORDS
    ]


def _tokenize_query(query: str) -> list[str]:
    """Split a user query into lowercased keyword terms."""
    seen: set[str] = set()
    terms: list[str] = []
    for token in _TOKEN_RE.findall(query.lower()):
        if len(token) < 2 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _resolve_doc_file(root: Path, route_path: str) -> Path | None:
    candidates = (
        root / f"{route_path}.mdx",
        root / f"{route_path}.md",
        root / route_path / "index.mdx",
        root / route_path / "index.md",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _build_doc_page(
    root: Path,
    route_path: str,
    *,
    breadcrumb: tuple[str, ...],
    order: int,
) -> DocPage | None:
    file_path = _resolve_doc_file(root, route_path)
    if file_path is None:
        return None
    try:
        contents = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    frontmatter, body = _split_frontmatter(contents)
    fallback = route_path.rsplit("/", 1)[-1].replace("-", " ").title()
    title = _extract_page_title(contents, fallback=fallback)
    description = _normalize_text(frontmatter.get("description"))
    llm_hint = _normalize_text(frontmatter.get("llm_hint"))
    aliases = _normalize_aliases(frontmatter.get("aliases"))
    content = body.strip()

    return DocPage(
        path=route_path,
        file_path=file_path.relative_to(root).as_posix(),
        title=title,
        description=description,
        llm_hint=llm_hint,
        aliases=aliases,
        breadcrumb=breadcrumb,
        content=content,
        sections=_extract_sections(content),
        order=order,
    )


def _score_counter(counter: Counter[str], term: str, *, weight: int, cap: int) -> int:
    return min(counter.get(term, 0), cap) * weight


def _normalized_phrase(text: str) -> str:
    return " ".join(_tokenize_text(text))


def _score_section(section: DocSection, terms: list[str]) -> int:
    title_counts = Counter(_tokenize_text(section.title))
    body_counts = Counter(_tokenize_text(section.content))
    score = 0
    matched_terms = 0
    for term in terms:
        term_score = _score_counter(
            title_counts, term, weight=7, cap=2
        ) + _score_counter(body_counts, term, weight=1, cap=4)
        if term_score:
            matched_terms += 1
            score += term_score
    score += matched_terms * 4

    phrase = " ".join(terms)
    if phrase and phrase in _normalized_phrase(section.content):
        score += 6
    return score


def _score_page(page: DocPage, terms: list[str]) -> tuple[int, DocSection | None]:
    if not terms:
        return 0, None

    path_counts = Counter(_tokenize_text(page.path))
    title_counts = Counter(_tokenize_text(page.title))
    breadcrumb_counts = Counter(_tokenize_text(" ".join(page.breadcrumb)))
    hint_counts = Counter(_tokenize_text(page.routing_hint()))
    alias_counts = Counter(_tokenize_text(" ".join(page.aliases)))

    score = 0
    matched_terms = 0
    for term in terms:
        term_score = (
            _score_counter(path_counts, term, weight=6, cap=3)
            + _score_counter(title_counts, term, weight=10, cap=2)
            + _score_counter(breadcrumb_counts, term, weight=4, cap=2)
            + _score_counter(hint_counts, term, weight=7, cap=3)
            + _score_counter(alias_counts, term, weight=7, cap=3)
        )
        if term_score:
            matched_terms += 1
            score += term_score

    best_section = None
    best_section_score = 0
    for section in page.sections:
        section_score = _score_section(section, terms)
        if section_score > best_section_score:
            best_section = section
            best_section_score = section_score

    if score == 0 and best_section_score == 0:
        return 0, None

    score += matched_terms * 8 + best_section_score

    phrase = " ".join(terms)
    if phrase:
        if phrase in _normalized_phrase(page.title):
            score += 12
        elif phrase in _normalized_phrase(page.routing_hint()):
            score += 8
        elif phrase in _normalized_phrase(page.path):
            score += 8
        elif best_section is not None and phrase in _normalized_phrase(
            best_section.content
        ):
            score += 4

    return score, best_section


def _set_descendant_counts(
    sections_by_path: dict[str, NavSection],
    section_path: str,
) -> int:
    section = sections_by_path[section_path]
    page_count = 0
    for child_kind, child_path in section.children:
        if child_kind == "page":
            page_count += 1
        else:
            page_count += _set_descendant_counts(sections_by_path, child_path)
    sections_by_path[section_path] = replace(section, descendant_page_count=page_count)
    return page_count


@lru_cache(maxsize=1)
def _docs_index() -> DocsIndex:
    root = _resolve_docs_root()
    if root is None:
        return DocsIndex(pages_by_path={}, sections_by_path={})

    try:
        docs_config = json.loads((root / "docs.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return DocsIndex(pages_by_path={}, sections_by_path={})

    pages_by_path: dict[str, DocPage] = {}
    sections_by_path: dict[str, NavSection] = {}
    page_order = 0

    def ensure_unique_section_path(base_path: str) -> str:
        if base_path not in sections_by_path:
            return base_path
        suffix = 2
        while f"{base_path}-{suffix}" in sections_by_path:
            suffix += 1
        return f"{base_path}-{suffix}"

    def walk_pages(
        items: list[Any],
        *,
        section_path: str,
        section_title: str,
        ancestor_breadcrumb: tuple[str, ...],
    ) -> None:
        nonlocal page_order
        children: list[tuple[str, str]] = []
        page_breadcrumb = ancestor_breadcrumb + (section_title,)

        for item in items:
            if isinstance(item, str):
                route_path = item.strip("/")
                if not route_path:
                    continue
                if route_path not in pages_by_path:
                    page = _build_doc_page(
                        root,
                        route_path,
                        breadcrumb=page_breadcrumb,
                        order=page_order,
                    )
                    if page is not None:
                        pages_by_path[route_path] = page
                        page_order += 1
                if route_path in pages_by_path:
                    children.append(("page", route_path))
                continue

            if not isinstance(item, dict):
                continue
            group_title = str(item.get("group", "")).strip()
            nested_pages = item.get("pages")
            if not group_title or not isinstance(nested_pages, list):
                continue

            child_path = ensure_unique_section_path(
                f"{section_path}/{_slugify(group_title)}"
            )
            walk_pages(
                nested_pages,
                section_path=child_path,
                section_title=group_title,
                ancestor_breadcrumb=page_breadcrumb,
            )
            children.append(("section", child_path))

        sections_by_path[section_path] = NavSection(
            path=section_path,
            title=section_title,
            breadcrumb=ancestor_breadcrumb,
            children=tuple(children),
        )

    root_children: list[tuple[str, str]] = []
    tabs = docs_config.get("navigation", {}).get("tabs", [])
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        tab_title = str(tab.get("tab", "")).strip() or "Docs"
        for group in tab.get("groups", []):
            if not isinstance(group, dict):
                continue
            group_title = str(group.get("group", "")).strip()
            group_pages = group.get("pages")
            if not group_title or not isinstance(group_pages, list):
                continue
            top_level_path = ensure_unique_section_path(
                f"{_slugify(tab_title)}/{_slugify(group_title)}"
            )
            walk_pages(
                group_pages,
                section_path=top_level_path,
                section_title=group_title,
                ancestor_breadcrumb=(tab_title,),
            )
            root_children.append(("section", top_level_path))

    sections_by_path[_ROOT_SECTION_PATH] = NavSection(
        path=_ROOT_SECTION_PATH,
        title="Docs",
        breadcrumb=(),
        children=tuple(root_children),
    )
    _set_descendant_counts(sections_by_path, _ROOT_SECTION_PATH)

    return DocsIndex(pages_by_path=pages_by_path, sections_by_path=sections_by_path)


def _get_page_or_404(path: str) -> DocPage:
    page = _docs_index().pages_by_path.get(path.strip("/"))
    if page is None:
        raise HTTPException(status_code=404, detail=f"Unknown docs page: {path!r}")
    return page


def _find_section(page: DocPage, section: str) -> DocSection | None:
    target = section.strip().lower()
    for candidate in page.sections:
        if candidate.slug.lower() == target or candidate.title.lower() == target:
            return candidate
    return None


def _expand_nav_entries(
    index: DocsIndex,
    section_path: str,
    depth: int,
) -> list[dict]:
    section = index.sections_by_path[section_path]
    results: list[dict] = []
    for child_kind, child_path in section.children:
        if child_kind == "section":
            child_section = index.sections_by_path[child_path]
            results.append(child_section.to_mcp_dict())
            if depth > 1:
                results.extend(_expand_nav_entries(index, child_path, depth - 1))
        else:
            results.append(index.pages_by_path[child_path].to_catalog_dict())
    return results


@traced_tool
async def list_docs(path: str | None = None, depth: int = 1) -> list[dict]:
    """Browse the Dograh docs hierarchy before reading a page in full.

    ``path`` addresses navigation sections exposed by this tool. Page paths
    returned by ``search_docs`` and ``read_doc`` are the published docs routes
    instead, for example ``voice-agent/tools/mcp-tool``.
    """
    await authenticate_mcp_request()

    if depth < 1 or depth > DOCS_LIST_MAX_DEPTH:
        raise ValueError(f"`depth` must be between 1 and {DOCS_LIST_MAX_DEPTH}.")

    index = _docs_index()
    if not index.sections_by_path:
        return []

    if path is None:
        return _expand_nav_entries(index, _ROOT_SECTION_PATH, depth)

    normalized = path.strip("/")
    if normalized in index.sections_by_path:
        return _expand_nav_entries(index, normalized, depth)
    if normalized in index.pages_by_path:
        return [index.pages_by_path[normalized].to_catalog_dict()]

    raise HTTPException(status_code=404, detail=f"Unknown docs section: {path!r}")


@traced_tool
async def read_doc(path: str, section: str | None = None) -> dict:
    """Read one docs page after you have narrowed to a likely match."""
    await authenticate_mcp_request()

    if not isinstance(path, str) or not path.strip():
        raise ValueError("`path` must be a non-empty string.")

    page = _get_page_or_404(path)
    active_section = None
    if section is not None:
        active_section = _find_section(page, section)
        if active_section is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown section {section!r} for docs page {path!r}",
            )
    return page.to_read_dict(section=active_section)


@traced_tool
async def search_docs(query: str, limit: int = 5) -> list[dict]:
    """Search the Dograh documentation and return a lean ranked shortlist.

    Use this first for keyword or acronym lookup. Once the right page looks
    likely, call ``read_doc(path)`` instead of reasoning from summaries alone.
    """
    await authenticate_mcp_request()

    if not isinstance(query, str) or not query.strip():
        raise ValueError("`query` must be a non-empty string.")
    if limit < 1:
        raise ValueError("`limit` must be at least 1.")

    terms = _tokenize_query(query)
    if not terms:
        raise ValueError(
            "`query` must contain at least one non-stopword alphanumeric term."
        )

    index = _docs_index()
    if not index.pages_by_path:
        return []

    capped_limit = min(limit, DOCS_SEARCH_MAX_LIMIT)
    ranked: list[tuple[int, int, DocPage, DocSection | None]] = []
    for page in index.pages_by_path.values():
        score, best_section = _score_page(page, terms)
        if score <= 0:
            continue
        ranked.append((score, page.order, page, best_section))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2].path))
    return [
        page.to_catalog_dict(section=best_section)
        for _, _, page, best_section in ranked[:capped_limit]
    ]
