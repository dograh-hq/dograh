"""`search_docs` MCP tool — keyword search over the Mintlify docs tree.

The docs are shipped into the API image (`COPY ./docs ./docs` in
`api/Dockerfile`), so this tool works for both source/dev runs and
Docker deployments. For source/dev runs we walk up from this file to
locate the `docs/` directory; for Docker we land on `/app/docs`. An
explicit `DOGRAH_DOCS_PATH` env var overrides discovery.

The implementation is intentionally dependency-free: it does in-memory
keyword scoring rather than building a vector index. The docs corpus is
small (~100 .mdx files, ~140k LoC), so a per-call scan is well under
50 ms and avoids needing an embedding backend, vector store, or
background indexer for a tool that's called interactively from MCP.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool

# Public site for the rendered docs. Used to build a clickable URL per
# result; agents can hand the URL back to the user even if the local
# file isn't reachable.
DOCS_SITE_BASE_URL = "https://docs.dograh.com"

# Hard cap regardless of caller-supplied limit. Keeps the MCP response
# payload bounded; Mintlify search APIs use a similar 10-25 ceiling.
DOCS_SEARCH_MAX_LIMIT = 25

# Heading-detection regex. Matches ATX headings (`# `, `## `, etc.) but
# not in-line `#` characters.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)


def _resolve_docs_root() -> Path | None:
    """Return the path to the on-disk docs tree, or None if not found.

    Resolution order:
    1. ``DOGRAH_DOCS_PATH`` env var (absolute path).
    2. ``/app/docs`` — the location the API Dockerfile copies docs to.
    3. Walk upward from this file looking for a sibling ``docs/`` dir
       (covers source-checkout / dev runs).
    """
    override = os.environ.get("DOGRAH_DOCS_PATH")
    if override:
        candidate = Path(override).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    docker_default = Path("/app/docs")
    if docker_default.is_dir():
        return docker_default

    # Walk up from .../api/mcp_server/tools/docs_search.py looking for docs/.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docs"
        if candidate.is_dir():
            return candidate

    return None


@lru_cache(maxsize=1)
def _docs_corpus() -> tuple[tuple[str, str], ...]:
    """Load the docs corpus once per process.

    Returns a tuple of ``(relative_path, file_contents)`` pairs. The
    docs tree is small and read-mostly at runtime, so caching the full
    text in memory is cheaper than re-reading on every search.
    Cache miss is intentional when ``DOGRAH_DOCS_PATH`` flips at
    startup — for live edits, restart the process.
    """
    root = _resolve_docs_root()
    if root is None:
        return ()

    pairs: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".mdx", ".md"}:
            continue
        try:
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Skip unreadable files rather than crashing the whole tool.
            continue
        rel = path.relative_to(root).as_posix()
        pairs.append((rel, contents))
    return tuple(pairs)


def _tokenize_query(query: str) -> list[str]:
    """Split a user query into lowercased keyword terms.

    Empty strings and 1-char filler terms are dropped — they would
    match almost every file and drown out the real signal.
    """
    terms = re.findall(r"[A-Za-z0-9_]+", query.lower())
    return [term for term in terms if len(term) >= 2]


def _extract_page_title(contents: str, fallback: str) -> str:
    """Pull a human-readable title for a docs page.

    Mintlify pages start with a YAML frontmatter block whose ``title``
    is the most authoritative title; fall back to the first ATX heading
    if frontmatter is missing or malformed; fall back to the filename
    if no heading exists.
    """
    if contents.startswith("---"):
        end = contents.find("---", 3)
        if end != -1:
            frontmatter = contents[3:end]
            for line in frontmatter.splitlines():
                line = line.strip()
                if line.lower().startswith("title:"):
                    value = line.split(":", 1)[1].strip()
                    # Strip surrounding quotes if Mintlify wrote them.
                    if (
                        len(value) >= 2
                        and value[0] == value[-1]
                        and value[0] in ('"', "'")
                    ):
                        value = value[1:-1]
                    if value:
                        return value

    match = _HEADING_RE.search(contents)
    if match:
        return match.group(2).strip()

    return fallback


def _strip_frontmatter(contents: str) -> str:
    """Drop the YAML frontmatter block from a docs page body."""
    if not contents.startswith("---"):
        return contents
    end = contents.find("---", 3)
    if end == -1:
        return contents
    return contents[end + 3 :].lstrip("\n")


def _build_snippet(body: str, terms: list[str], snippet_radius: int = 120) -> str:
    """Return a ~240-char window around the first term hit in ``body``.

    The window is centered on the earliest match (whichever term comes
    first wins) so the snippet shows context for the strongest signal,
    not the lexicographically-first term. Leading/trailing newlines are
    collapsed so the snippet renders cleanly through MCP's text payload.
    """
    body_lower = body.lower()
    earliest = -1
    for term in terms:
        idx = body_lower.find(term)
        if idx != -1 and (earliest == -1 or idx < earliest):
            earliest = idx

    if earliest == -1:
        # No hit in body — the match must have come from the title or
        # path, so just return the first line of body as orientation.
        first_line = next(
            (line.strip() for line in body.splitlines() if line.strip()),
            "",
        )
        return first_line[: snippet_radius * 2]

    start = max(0, earliest - snippet_radius)
    end = min(len(body), earliest + snippet_radius)
    snippet = body[start:end]
    # Collapse all whitespace runs (incl. internal newlines) for a
    # single-line snippet — MCP renders text payloads inline.
    snippet = " ".join(snippet.split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{snippet}{suffix}"


def _score_page(
    rel_path: str,
    title: str,
    body: str,
    terms: list[str],
) -> int:
    """Weighted keyword score for a single docs page.

    Title/path matches outweigh body matches because they encode the
    page's purpose, not just incidental mentions. Each query term
    contributes independently — a page matching all terms ranks above
    one matching a single term many times.
    """
    if not terms:
        return 0
    score = 0
    path_lower = rel_path.lower()
    title_lower = title.lower()
    body_lower = body.lower()
    for term in terms:
        path_hits = path_lower.count(term)
        title_hits = title_lower.count(term)
        body_hits = body_lower.count(term)
        if path_hits == 0 and title_hits == 0 and body_hits == 0:
            # Penalize pages that miss any query term — they probably
            # aren't what the caller wants.
            continue
        # Diminishing returns past a few hits per term: 1 dominant page
        # shouldn't outweigh a page that hits every term. The cap is
        # deliberately set so ``title_weight (5)`` strictly exceeds
        # ``body_cap (4) × body_weight (1)`` — a page whose TITLE is the
        # term must outrank a page that merely mentions it repeatedly.
        body_hits = min(body_hits, 4)
        score += path_hits * 8 + title_hits * 5 + body_hits
    return score


def _docs_url_for(rel_path: str) -> str:
    """Build the public docs URL for a relative on-disk path."""
    # Strip the extension and `index` so `getting-started/index.mdx`
    # maps to `/getting-started`, matching Mintlify's routing.
    no_ext = re.sub(r"\.(mdx|md)$", "", rel_path, flags=re.IGNORECASE)
    if no_ext.endswith("/index"):
        no_ext = no_ext[: -len("/index")]
    return f"{DOCS_SITE_BASE_URL}/{no_ext}".rstrip("/")


@traced_tool
async def search_docs(query: str, limit: int = 10) -> list[dict]:
    """Search the Dograh documentation by keyword and return ranked pages.

    Use this when the caller asks "how do I configure X" / "where are the docs for Y" /
    "what does Dograh say about Z" — anything that should land on a docs page
    rather than a workspace resource. For workspace data (agents, recordings,
    credentials), use ``list_workflows`` / ``list_recordings`` / ``list_credentials``
    instead.

    Args:
        query: Free-form keywords (e.g. "TURN server", "elevenlabs voice").
            Tokenized on non-alphanumeric characters; terms shorter than
            2 characters are dropped.
        limit: Max pages to return. Capped at 25 regardless of input;
            default 10 keeps the payload small enough to inline in MCP.

    Returns:
        Up to ``limit`` results, sorted by descending relevance score.
        Each entry has:
          * ``path`` — repo-relative path (e.g. ``configurations/voice.mdx``)
          * ``url`` — public docs URL (https://docs.dograh.com/...)
          * ``title`` — page title (from Mintlify frontmatter when present)
          * ``score`` — opaque integer relevance score
          * ``snippet`` — ~240-char excerpt around the first term hit
    """
    # Authentication is consistent with the rest of the MCP tools and
    # routes through the same rate-limiting path, even though docs are
    # not org-scoped data.
    await authenticate_mcp_request()

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string.")

    try:
        effective_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer.") from exc
    if effective_limit < 1:
        raise ValueError("limit must be at least 1.")
    effective_limit = min(effective_limit, DOCS_SEARCH_MAX_LIMIT)

    terms = _tokenize_query(query)
    if not terms:
        # The caller passed something like punctuation-only or only
        # single-char tokens — surface an actionable error rather than
        # silently returning everything.
        raise ValueError(
            "query must contain at least one keyword of 2+ alphanumeric characters."
        )

    corpus = _docs_corpus()
    if not corpus:
        # Tool is registered but docs aren't on disk — return empty
        # rather than 500ing so the caller can degrade gracefully.
        return []

    scored: list[tuple[int, str, str, str]] = []
    for rel_path, contents in corpus:
        title = _extract_page_title(contents, fallback=rel_path)
        body = _strip_frontmatter(contents)
        score = _score_page(rel_path, title, body, terms)
        if score <= 0:
            continue
        scored.append((score, rel_path, title, body))

    scored.sort(key=lambda item: (-item[0], item[1]))

    results: list[dict] = []
    for score, rel_path, title, body in scored[:effective_limit]:
        results.append(
            {
                "path": rel_path,
                "url": _docs_url_for(rel_path),
                "title": title,
                "score": score,
                "snippet": _build_snippet(body, terms),
            }
        )
    return results
