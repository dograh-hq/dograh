"""End-to-end tests for the Node TS validator bridge.

Exercises the real `node` subprocess — slow-ish but the whole point is
that code → JSON and JSON → code round-trip losslessly.
"""

from __future__ import annotations

import shutil

import pytest

from api.mcp_server.ts_bridge import TsBridgeError, generate_code, parse_code

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node binary not available"
)


def _minimal_workflow() -> dict:
    """Start → End, one edge. Stored shape matches ReactFlowDTO."""
    return {
        "nodes": [
            {
                "id": "1",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Greeting",
                    "prompt": "Greet warmly.",
                    "greeting_type": "text",
                    "greeting": "Hi {{first_name}}!",
                    "allow_interrupt": True,
                },
            },
            {
                "id": "2",
                "type": "endCall",
                "position": {"x": 200, "y": 0},
                "data": {"name": "Done", "prompt": "Say goodbye."},
            },
        ],
        "edges": [
            {
                "id": "1-2",
                "source": "1",
                "target": "2",
                "data": {"label": "done", "condition": "conversation complete"},
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def _normalize(wf: dict) -> dict:
    """Strip cosmetics before comparing a round-tripped workflow.

    Node IDs are regenerated deterministically by the parser
    (1, 2, 3, ...) so the inputs already match if constructed that way.
    Position is preserved. Edge ids follow `source-target`.
    """
    return {
        "nodes": [
            {
                "id": n["id"],
                "type": n["type"],
                "position": n["position"],
                "data": n["data"],
            }
            for n in wf["nodes"]
        ],
        "edges": [
            {
                "id": e["id"],
                "source": e["source"],
                "target": e["target"],
                "data": e["data"],
            }
            for e in wf["edges"]
        ],
    }


# ─── generate_code ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_emits_imports_and_factories():
    code = await generate_code(_minimal_workflow(), workflow_name="test")
    assert 'import { Workflow } from "@dograh/sdk";' in code
    assert "startCall" in code
    assert "endCall" in code
    assert "wf.addTyped(startCall(" in code
    assert "wf.edge(" in code


@pytest.mark.asyncio
async def test_generate_strips_spec_defaults():
    wf = _minimal_workflow()
    code = await generate_code(wf)
    # `add_global_prompt=True` is a spec default for startCall; emitted
    # code should omit it. Keeps the LLM-facing projection tight.
    assert "add_global_prompt" not in code


@pytest.mark.asyncio
async def test_generate_omits_position():
    """Positions are hidden from the LLM — auto-layout post-processing
    (future) reassigns them on save. Keeping them out of the edit
    surface avoids the LLM producing cramped/overlapping layouts."""
    wf = _minimal_workflow()
    code = await generate_code(wf)
    assert "position" not in code


@pytest.mark.asyncio
async def test_generate_strips_legacy_ui_state_fields():
    """Stored workflows from before spec validation carry UI-state fields
    (`invalid`, `selected`, `is_start`, etc.). `get_workflow_code` hides
    those from the LLM so edits don't round-trip the noise."""
    wf = {
        "nodes": [
            {
                "id": "1",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "g",
                    "prompt": "hi",
                    "invalid": False,
                    "validationMessage": None,
                    "is_start": True,
                    "selected": True,
                    "dragging": False,
                },
            },
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    code = await generate_code(wf)
    for dropped in ("invalid", "validationMessage", "is_start", "selected", "dragging"):
        assert dropped not in code, f"{dropped} should be stripped"
    assert 'prompt: "hi"' in code


@pytest.mark.asyncio
async def test_generate_strips_unknown_edge_fields():
    wf = _minimal_workflow()
    wf["edges"][0]["data"]["invalid"] = False
    wf["edges"][0]["data"]["validationMessage"] = None
    code = await generate_code(wf)
    assert "invalid" not in code
    assert "validationMessage" not in code


# ─── parse_code ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_accepts_minimal_code():
    code = """import { Workflow } from "@dograh/sdk";
import { startCall, endCall } from "@dograh/sdk/typed";

const wf = new Workflow({ name: "min" });
const a = wf.addTyped(startCall({ name: "g", prompt: "hi" }));
const b = wf.addTyped(endCall({ name: "d", prompt: "bye" }));
wf.edge(a, b, { label: "done", condition: "wrapped" });
"""
    result = await parse_code(code)
    assert result["ok"] is True
    wf = result["workflow"]
    assert len(wf["nodes"]) == 2
    assert len(wf["edges"]) == 1
    assert wf["nodes"][0]["type"] == "startCall"
    assert wf["edges"][0]["source"] == wf["nodes"][0]["id"]


@pytest.mark.asyncio
async def test_parse_rejects_function_declaration():
    code = """import { Workflow } from "@dograh/sdk";
const wf = new Workflow({ name: "x" });
function evil() { return 1; }
"""
    result = await parse_code(code)
    assert result["ok"] is False
    assert result["stage"] == "parse"
    assert any("FunctionDeclaration" in e["message"] for e in result["errors"])


@pytest.mark.asyncio
async def test_parse_rejects_unknown_field():
    code = """import { Workflow } from "@dograh/sdk";
import { startCall } from "@dograh/sdk/typed";
const wf = new Workflow({ name: "x" });
const a = wf.addTyped(startCall({ name: "g", prompt: "hi", promt: "typo" }));
"""
    result = await parse_code(code)
    assert result["ok"] is False
    assert result["stage"] == "validate"
    assert any("Unknown field" in e["message"] for e in result["errors"])


@pytest.mark.asyncio
async def test_parse_rejects_unknown_variable_in_edge():
    code = """import { Workflow } from "@dograh/sdk";
import { startCall, endCall } from "@dograh/sdk/typed";
const wf = new Workflow({ name: "x" });
const a = wf.addTyped(startCall({ name: "g", prompt: "hi" }));
wf.edge(a, missing, { label: "done", condition: "c" });
"""
    result = await parse_code(code)
    assert result["ok"] is False
    assert result["stage"] == "parse"
    assert any("Unknown node variable" in e["message"] for e in result["errors"])


@pytest.mark.asyncio
async def test_parse_requires_label_and_condition_on_edge():
    code = """import { Workflow } from "@dograh/sdk";
import { startCall, endCall } from "@dograh/sdk/typed";
const wf = new Workflow({ name: "x" });
const a = wf.addTyped(startCall({ name: "g", prompt: "hi" }));
const b = wf.addTyped(endCall({ name: "d", prompt: "bye" }));
wf.edge(a, b, { label: "", condition: "c" });
"""
    result = await parse_code(code)
    assert result["ok"] is False
    assert result["stage"] == "parse"


# ─── Round-trip ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_round_trip_minimal():
    wf = _minimal_workflow()
    code = await generate_code(wf, workflow_name="rt")
    result = await parse_code(code)
    assert result["ok"] is True, result
    # Positions are intentionally not preserved — they'll be reassigned
    # by a downstream auto-layout pass. Parser defaults to {0, 0}.
    for in_node, out_node in zip(wf["nodes"], result["workflow"]["nodes"]):
        assert out_node["type"] == in_node["type"]
        assert out_node["position"] == {"x": 0, "y": 0}
        for k, v in in_node["data"].items():
            assert out_node["data"][k] == v, (
                f"{k}: {out_node['data'].get(k)!r} != {v!r}"
            )
    assert _normalize({"nodes": [], "edges": result["workflow"]["edges"]})["edges"] == [
        {
            "id": "1-2",
            "source": "1",
            "target": "2",
            "data": {"label": "done", "condition": "conversation complete"},
        }
    ]


@pytest.mark.asyncio
async def test_generate_fails_on_unknown_type():
    bad = {
        "nodes": [
            {
                "id": "1",
                "type": "doesNotExist",
                "position": {"x": 0, "y": 0},
                "data": {},
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    with pytest.raises(TsBridgeError, match="Unknown node type"):
        await generate_code(bad)
