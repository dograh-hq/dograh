from pathlib import Path

import pytest

from api.services.workflow.dto import ReactFlowDTO, sanitize_workflow_definition

_FIXTURES_DIR = Path(__file__).parent / "dto_fixtures"


@pytest.mark.asyncio
async def test_dto():
    # Path resolved relative to this test file so the test works regardless
    # of the cwd pytest is invoked from.
    with open(_FIXTURES_DIR / "sample_branching_workflow.json", "r") as f:
        dto = ReactFlowDTO.model_validate_json(f.read())
    assert dto is not None


def test_sanitize_strips_ui_runtime_fields():
    definition = {
        "viewport": {"x": 0, "y": 0, "zoom": 1},
        "nodes": [
            {
                "id": "n1",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "width": 200,  # ReactFlow-computed, preserved
                "selected": True,  # ReactFlow runtime, preserved
                "data": {
                    "name": "Start",
                    "prompt": "hi",
                    "greeting": "hello",
                    "invalid": True,  # UI-only, should be stripped
                    "validationMessage": "oops",  # UI-only, should be stripped
                    "mystery_field": 42,  # unknown, should be stripped
                },
            },
            {
                "id": "n2",
                "type": "agentNode",
                "position": {"x": 1, "y": 1},
                "data": {"name": "A", "prompt": "p", "invalid": False},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "n1",
                "target": "n2",
                "data": {
                    "label": "next",
                    "condition": "true",
                    "invalid": True,  # UI-only, should be stripped
                },
            }
        ],
    }

    out = sanitize_workflow_definition(definition)

    # Top-level keys preserved
    assert out["viewport"] == {"x": 0, "y": 0, "zoom": 1}
    # ReactFlow runtime fields on the node itself preserved
    assert out["nodes"][0]["width"] == 200
    assert out["nodes"][0]["selected"] is True

    # node.data stripped of unknowns, known fields kept
    n1_data = out["nodes"][0]["data"]
    assert n1_data == {"name": "Start", "prompt": "hi", "greeting": "hello"}
    assert "invalid" not in n1_data
    assert "validationMessage" not in n1_data
    assert "mystery_field" not in n1_data

    n2_data = out["nodes"][1]["data"]
    assert n2_data == {"name": "A", "prompt": "p"}

    # edge.data stripped
    assert out["edges"][0]["data"] == {"label": "next", "condition": "true"}


def test_sanitize_noop_on_empty_and_unknown_types():
    assert sanitize_workflow_definition(None) is None
    assert sanitize_workflow_definition({}) == {}

    # Unknown node type: pass through unchanged rather than wipe data
    definition = {
        "nodes": [
            {
                "id": "n1",
                "type": "unknownType",
                "position": {"x": 0, "y": 0},
                "data": {"anything": "goes"},
            }
        ],
        "edges": [],
    }
    out = sanitize_workflow_definition(definition)
    assert out["nodes"][0]["data"] == {"anything": "goes"}
