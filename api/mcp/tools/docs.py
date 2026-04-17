import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from rank_bm25 import BM25Okapi

from api.mcp.server import mcp

DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_TITLE_RE = re.compile(r"^title:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _extract_title(path: Path, body: str) -> str:
    fm_match = _FRONTMATTER_RE.match(body)
    if fm_match:
        title_match = _TITLE_RE.search(fm_match.group(1))
        if title_match:
            return title_match.group(1).strip()
    h1_match = _H1_RE.search(body)
    if h1_match:
        return h1_match.group(1).strip()
    return path.stem.replace("-", " ").title()


def _strip_frontmatter(body: str) -> str:
    return _FRONTMATTER_RE.sub("", body, count=1)


@lru_cache(maxsize=1)
def _load_index() -> tuple[list[dict], BM25Okapi]:
    """Read every docs/**/*.mdx file once and build a BM25 index.

    Cached for the process lifetime — docs rarely change between restarts.
    """
    docs: list[dict] = []
    corpus: list[list[str]] = []

    for path in sorted(DOCS_ROOT.rglob("*.mdx")):
        body = path.read_text(encoding="utf-8")
        rel = path.relative_to(DOCS_ROOT).as_posix()
        title = _extract_title(path, body)
        content = _strip_frontmatter(body)
        docs.append({"path": rel, "title": title, "content": content})
        corpus.append(_tokenize(f"{title} {content}"))

    return docs, BM25Okapi(corpus)


def _snippet(content: str, query_tokens: list[str], width: int = 240) -> str:
    lowered = content.lower()
    for tok in query_tokens:
        idx = lowered.find(tok)
        if idx >= 0:
            start = max(0, idx - width // 2)
            end = min(len(content), start + width)
            return (
                ("…" if start > 0 else "")
                + content[start:end].strip()
                + ("…" if end < len(content) else "")
            )
    return content[:width].strip() + ("…" if len(content) > width else "")


@mcp.tool
async def search_dograh_docs(query: str, limit: int = 5) -> list[dict]:
    """Search Dograh's product documentation.

    Returns the top matches as {path, title, snippet}. Pass the returned
    `path` to `fetch_dograh_doc` to read the full page. Use this first
    when you need to learn how a Dograh feature works before building
    against it.
    """
    docs, bm25 = _load_index()
    tokens = _tokenize(query)
    if not tokens:
        return []

    scores = bm25.get_scores(tokens)
    ranked = sorted(zip(scores, docs), key=lambda pair: pair[0], reverse=True)[:limit]

    return [
        {
            "path": doc["path"],
            "title": doc["title"],
            "snippet": _snippet(doc["content"], tokens),
            "score": round(float(score), 3),
        }
        for score, doc in ranked
        if score > 0
    ]


@mcp.tool
async def fetch_dograh_doc(path: str) -> dict:
    """Fetch the full content of a Dograh docs page by its path
    (e.g. `core-concepts/workflows.mdx`), as returned by `search_dograh_docs`.
    """
    docs, _ = _load_index()
    for doc in docs:
        if doc["path"] == path:
            return {
                "path": doc["path"],
                "title": doc["title"],
                "content": doc["content"],
            }
    raise HTTPException(status_code=404, detail=f"Doc not found: {path}")
