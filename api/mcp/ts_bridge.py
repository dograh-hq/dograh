"""Python-side bridge to the Node TS validator.

Spawns `node api/mcp/ts_validator/src/index.ts` as a short-lived
subprocess per call, streams a JSON request on stdin, reads a JSON
response from stdout. The validator never executes LLM code — it either
emits TypeScript from a workflow JSON (`generate`) or parses LLM-authored
TS back into a workflow JSON via AST walking (`parse`).

The subprocess startup cost is ~100-200ms per call. Fine for MCP tool
rates; if it ever matters, the validator can be promoted to a long-lived
worker over a unix socket without changing this interface.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from api.services.workflow.node_specs import all_specs

_VALIDATOR_ENTRY = Path(__file__).resolve().parent / "ts_validator" / "src" / "index.ts"


class TsBridgeError(Exception):
    """The Node subprocess failed before producing a JSON response."""


def _specs_payload() -> list[dict[str, Any]]:
    return [s.model_dump(mode="json") for s in all_specs()]


async def _invoke(request: dict[str, Any]) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        "node",
        str(_VALIDATOR_ENTRY),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(json.dumps(request).encode("utf-8"))
    if proc.returncode != 0 and not stdout:
        raise TsBridgeError(
            f"ts_validator exited {proc.returncode}: "
            f"{stderr.decode('utf-8', errors='replace')}"
        )
    try:
        return json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise TsBridgeError(
            f"ts_validator emitted non-JSON: {stdout!r} (stderr: {stderr!r})"
        ) from e


async def generate_code(workflow: dict[str, Any], *, workflow_name: str = "") -> str:
    """Emit SDK TypeScript source from a workflow JSON payload.

    Raises `TsBridgeError` if the validator can't produce code (unknown
    node type, dangling edge reference, etc.) — these are bugs at the
    caller layer, not user input, so we fail loudly.
    """
    result = await _invoke(
        {
            "command": "generate",
            "workflow": workflow,
            "specs": _specs_payload(),
            "workflowName": workflow_name,
        }
    )
    if not result.get("ok"):
        errs = result.get("errors") or [{"message": "unknown failure"}]
        raise TsBridgeError(
            "generate_code failed: " + "; ".join(e.get("message", "") for e in errs)
        )
    return result["code"]


async def parse_code(code: str) -> dict[str, Any]:
    """Parse LLM-authored TS back into a workflow JSON.

    Returns the raw validator response — `{"ok": True, "workflow": {...}}`
    on success, `{"ok": False, "stage": "parse" | "validate", "errors": [...]}`
    on author-side failure. Author-side failures are surfaced to the LLM
    verbatim so it can iterate; callers should not re-wrap them.
    """
    return await _invoke(
        {
            "command": "parse",
            "code": code,
            "specs": _specs_payload(),
        }
    )
