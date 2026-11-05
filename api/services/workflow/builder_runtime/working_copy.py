"""Exact edits to session-owned SDK source; never mutates the saved workflow."""

import difflib
import hashlib
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field

MAX_SOURCE_CHARACTERS = 100_000


class SourceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_text: str = Field(min_length=1)
    new_text: str


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
    """Compatibility for single edits stored in older checkpoints."""
    if not old_text:
        return {"error": "Provide non-empty old_text and a different replacement."}
    return patch_source(
        code, revision, [SourceEdit(old_text=old_text, new_text=new_text)]
    )


def patch_source(code: str, revision: str, edits: list[SourceEdit]) -> dict:
    """Apply unique, non-overlapping replacements against one source revision."""
    current = source_revision(code)
    if revision != current:
        return {
            "error": "The working copy changed. Read it again before editing.",
            "revision": current,
        }
    regions = []
    for index, edit in enumerate(edits):
        matches = code.count(edit.old_text)
        if matches != 1 or edit.old_text == edit.new_text:
            return {
                "error": f"Edit {index + 1}: expected one exact match and a changed replacement; found {matches} matches. No edits applied. Read the source and include unique surrounding context.",
                "revision": current,
            }
        start = code.index(edit.old_text)
        regions.append((start, start + len(edit.old_text), edit.new_text))
    regions.sort()
    if any(left[1] > right[0] for left, right in pairwise(regions)):
        return {
            "error": "Edits overlap. Combine them into one replacement. No edits applied.",
            "revision": current,
        }
    updated = code
    for start, end, replacement in reversed(regions):
        updated = updated[:start] + replacement + updated[end:]
    if len(updated) > MAX_SOURCE_CHARACTERS:
        return {"error": "Workflow source exceeds 100,000 characters."}
    return {
        "code": updated,
        "revision": source_revision(updated),
        "edits_applied": len(edits),
        "diff": "".join(
            difflib.unified_diff(
                code.splitlines(True),
                updated.splitlines(True),
                fromfile="before",
                tofile="after",
            )
        ),
    }
