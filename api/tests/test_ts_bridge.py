"""End-to-end tests for the Node TS validator bridge.

Exercises the real `node` subprocess — slow-ish but the whole point is
that code → JSON and JSON → code round-trip losslessly.
"""

from __future__ import annotations

import shutil
from types import NoneType
from typing import Any, get_args

import pytest

from api.mcp_server.ts_bridge import TsBridgeError, generate_code, parse_code
from api.services.workflow.dto import EdgeDataDTO
from api.services.workflow.node_specs import (
    NodeSpec,
    PropertySpec,
    PropertyType,
    all_specs,
)

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


def _strip_optional(annotation: Any) -> Any:
    args = tuple(arg for arg in get_args(annotation) if arg is not NoneType)
    if len(args) == 1:
        return args[0]
    return annotation


def _pick_option_value(prop: PropertySpec) -> Any:
    assert prop.options, f"{prop.name} has no options"
    default = prop.default
    for option in prop.options:
        if option.value != default:
            return option.value
    return prop.options[0].value


def _sample_number(prop: PropertySpec) -> int | float:
    candidates: list[int | float] = [1, 2, 3, 0.5, 4.5, 10]
    for candidate in candidates:
        if prop.min_value is not None and candidate < prop.min_value:
            continue
        if prop.max_value is not None and candidate > prop.max_value:
            continue
        if prop.default is not None and candidate == prop.default:
            continue
        return candidate
    raise AssertionError(f"No valid sample number found for {prop.name}")


def _sample_property_value(prop: PropertySpec, *, path: str) -> Any:
    slug = path.replace(".", "_")

    if prop.type == PropertyType.string:
        return f"{slug}_value"
    if prop.type == PropertyType.mention_textarea:
        return f"{slug} prompt with {{name}}"
    if prop.type == PropertyType.url:
        return f"https://example.com/{slug}"
    if prop.type == PropertyType.recording_ref:
        return f"recording_{slug}"
    if prop.type == PropertyType.credential_ref:
        return f"credential_{slug}"
    if prop.type == PropertyType.number:
        return _sample_number(prop)
    if prop.type == PropertyType.boolean:
        return not prop.default if isinstance(prop.default, bool) else True
    if prop.type == PropertyType.options:
        return _pick_option_value(prop)
    if prop.type == PropertyType.multi_options:
        return [_pick_option_value(prop)]
    if prop.type == PropertyType.tool_refs:
        return [f"tool_{slug}"]
    if prop.type == PropertyType.document_refs:
        return [f"document_{slug}"]
    if prop.type == PropertyType.json:
        return {"kind": slug, "enabled": True}
    if prop.type == PropertyType.fixed_collection:
        assert prop.properties, f"{prop.name} fixed_collection has no sub-properties"
        return [
            {
                sub_prop.name: _sample_property_value(
                    sub_prop, path=f"{path}.{sub_prop.name}"
                )
                for sub_prop in prop.properties
            }
        ]
    raise AssertionError(f"Unhandled PropertyType in TS bridge test: {prop.type}")


def _sample_node_data(spec: NodeSpec) -> dict[str, Any]:
    return {
        prop.name: _sample_property_value(prop, path=f"{spec.name}.{prop.name}")
        for prop in spec.properties
    }


def _sample_edge_value(field_name: str, annotation: Any) -> Any:
    inner = _strip_optional(annotation)
    if inner is str:
        return f"{field_name}_value"
    if inner is bool:
        return True
    if inner in (int, float):
        return 1
    raise AssertionError(
        f"Unhandled edge field annotation in TS bridge test: {field_name} -> {annotation!r}"
    )


def _sample_edge_data() -> dict[str, Any]:
    return {
        field_name: _sample_edge_value(field_name, field.annotation)
        for field_name, field in EdgeDataDTO.model_fields.items()
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


@pytest.mark.asyncio
async def test_generate_preserves_all_edge_dto_fields():
    wf = _minimal_workflow()
    edge_data = _sample_edge_data()
    wf["edges"][0]["data"] = edge_data

    code = await generate_code(wf)
    result = await parse_code(code)

    assert result["ok"] is True, result
    assert result["workflow"]["edges"][0]["data"] == edge_data


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


@pytest.mark.asyncio
async def test_parse_rejects_unknown_edge_field():
    code = """import { Workflow } from "@dograh/sdk";
import { startCall, endCall } from "@dograh/sdk/typed";
const wf = new Workflow({ name: "x" });
const a = wf.addTyped(startCall({ name: "g", prompt: "hi" }));
const b = wf.addTyped(endCall({ name: "d", prompt: "bye" }));
wf.edge(a, b, { label: "done", condition: "wrapped", bogus: "x" });
"""
    result = await parse_code(code)
    assert result["ok"] is False
    assert result["stage"] == "parse"
    assert any("Unknown edge field" in e["message"] for e in result["errors"])


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
@pytest.mark.parametrize("spec", all_specs(), ids=lambda spec: spec.name)
async def test_round_trip_preserves_all_node_spec_fields(spec: NodeSpec):
    data = _sample_node_data(spec)
    wf = {
        "nodes": [
            {
                "id": "1",
                "type": spec.name,
                "position": {"x": 0, "y": 0},
                "data": data,
            }
        ],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }

    code = await generate_code(wf, workflow_name=f"{spec.name}_rt")
    result = await parse_code(code)

    assert result["ok"] is True, result
    assert result["workflow"]["nodes"][0]["data"] == data


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
