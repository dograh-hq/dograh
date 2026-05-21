"""Drift guards between the static MCP guide and the live tool surface.

`api/mcp_server/instructions.py` is free text baked into the client
system prompt. It is *not* the authoritative description of the tools —
names, signatures, and per-tool error codes reach the model dynamically
via `tools/list`, derived from each tool's own function signature and
docstring. These tests fail on the two classic drift modes:

1. The guide references a tool that is no longer registered (renamed or
   removed) — the model would be told to call something that 404s.
2. A tool returns an `error_code` that is absent from the description it
   ships via `tools/list` — the model can't learn to recover from it.

Keep the guide about orchestration (call order, hard constraints) and let
the tools describe themselves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api.mcp_server import instructions as instructions_module
from api.mcp_server.server import mcp
from api.mcp_server.tools import create_workflow as create_workflow_module
from api.mcp_server.tools import save_workflow as save_workflow_module

# Every registered MCP tool name starts with one of these verbs. A
# backticked snake_case token in the guide whose leading word is a verb is
# treated as a tool reference; field/reference names like `tool_refs`,
# `credential_ref`, or `pre_call_fetch` don't start with a verb and are
# correctly ignored. Extend this only when a new tool introduces a new
# leading verb (a missing verb under-checks, it never false-fails).
_TOOL_VERB_PREFIXES = frozenset(
    {
        "search",
        "read",
        "list",
        "get",
        "save",
        "create",
        "update",
        "delete",
        "add",
        "remove",
        "set",
    }
)

# A backtick immediately followed by a snake_case identifier (>= 1
# underscore). Anchoring on the opening backtick captures the leading
# identifier of a code span whether it is bare (`read_doc`) or a call
# (`read_doc(path)`), while skipping DSL constructs like `wf.edge` or
# `new Workflow` whose first char after the backtick isn't `[a-z_]`.
_BACKTICKED_SNAKE_RE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)")

# Error codes are emitted as the first string arg to `_error_result(...)`.
_ERROR_RESULT_LITERAL_RE = re.compile(r'_error_result\(\s*"([a-z_]+)"')
# `parse_error` / `validation_error` are picked by a `code_key` ternary
# rather than passed as a literal to `_error_result`, so match them too.
_CODE_KEY_LITERAL_RE = re.compile(r'"(parse_error|validation_error)"')


def _referenced_tool_names(text: str) -> set[str]:
    return {
        token
        for token in _BACKTICKED_SNAKE_RE.findall(text)
        if token.split("_", 1)[0] in _TOOL_VERB_PREFIXES
    }


def _returned_error_codes(module) -> set[str]:
    source = Path(module.__file__).read_text(encoding="utf-8")
    return set(_ERROR_RESULT_LITERAL_RE.findall(source)) | set(
        _CODE_KEY_LITERAL_RE.findall(source)
    )


@pytest.mark.asyncio
async def test_guide_only_references_registered_tools():
    registered = {tool.name for tool in await mcp.list_tools()}
    referenced = _referenced_tool_names(instructions_module.DOGRAH_MCP_INSTRUCTIONS)

    assert referenced, "no tool references extracted — the regex likely broke"
    unknown = sorted(referenced - registered)
    assert not unknown, (
        f"instructions.py references tools that are not registered: {unknown}. "
        f"Rename/remove the reference or register the tool. "
        f"Registered tools: {sorted(registered)}."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name, module",
    [
        ("save_workflow", save_workflow_module),
        ("create_workflow", create_workflow_module),
    ],
)
async def test_tool_documents_every_error_code_it_returns(tool_name, module):
    descriptions = {
        tool.name: tool.description or "" for tool in await mcp.list_tools()
    }
    description = descriptions[tool_name]
    returned = _returned_error_codes(module)

    assert returned, f"no error codes detected in {tool_name} source — regex broke"
    undocumented = sorted(code for code in returned if code not in description)
    assert not undocumented, (
        f"{tool_name} returns error_code(s) {undocumented} absent from the description "
        f"shipped via tools/list. Document them in the {tool_name} docstring."
    )
