"""Exact edits to session-owned SDK source; never mutates the saved workflow."""

import difflib
import hashlib

MAX_SOURCE_CHARACTERS = 100_000


def source_revision(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def read_source(code: str, start_line: int = 1, limit: int = 120) -> dict:
    lines = code.splitlines()
    end = min(len(lines), start_line - 1 + limit)
    return {
        "revision": source_revision(code),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": end,
        "content": "\n".join(
            f"{i + 1}: {lines[i]}" for i in range(start_line - 1, end)
        ),
        "has_more": end < len(lines),
    }


def edit_source(code: str, revision: str, old_text: str, new_text: str) -> dict:
    current = source_revision(code)
    if revision != current:
        return {
            "error": "The working copy changed. Read it again before editing.",
            "revision": current,
        }
    if not old_text or old_text == new_text:
        return {"error": "Provide non-empty old_text and a different replacement."}
    matches = code.count(old_text)
    if matches != 1:
        return {
            "error": f"Expected one exact match; found {matches}. Read the source and include unique surrounding context."
        }
    updated = code.replace(old_text, new_text, 1)
    if len(updated) > MAX_SOURCE_CHARACTERS:
        return {"error": "Workflow source exceeds 100,000 characters."}
    return {
        "code": updated,
        "revision": source_revision(updated),
        "diff": "".join(
            difflib.unified_diff(
                code.splitlines(True),
                updated.splitlines(True),
                fromfile="before",
                tofile="after",
            )
        ),
    }
