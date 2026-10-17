import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.services.workflow.dto import (
    ReactFlowDTO,
    StartCallNodeData,
    sanitize_workflow_definition,
)
from api.services.workflow.greeting_validation import validate_greeting_recordings
from api.services.workflow.workflow_graph import WorkflowGraph

_FIXTURES_DIR = Path(__file__).parent / "dto_fixtures"


@pytest.mark.parametrize(
    "recording_fields",
    [
        {},
        {"greeting_recording_id": None},
        {"greeting_recording_id": ""},
        {"greeting_recording_id": "  "},
    ],
)
def test_missing_recording_is_an_authoring_error_not_a_runtime_parse_error(
    recording_fields,
):
    definition = json.loads((_FIXTURES_DIR / "clean.json").read_text())
    definition["nodes"][0]["data"].update(greeting_type="audio", **recording_fields)
    dto = ReactFlowDTO.model_validate(definition)
    assert WorkflowGraph(dto).start_node_id == "s"
    errors = validate_greeting_recordings(definition)
    assert len(errors) == 1
    assert errors[0]["id"] == "s"
    assert errors[0]["field"] == "data.greeting_recording_id"


@pytest.mark.parametrize(
    "greeting_type,recording_id", [(None, None), ("text", None), ("audio", "42")]
)
def test_valid_greetings_pass_authoring_validation(greeting_type, recording_id):
    definition = json.loads((_FIXTURES_DIR / "clean.json").read_text())
    definition["nodes"][0]["data"].update(
        greeting_type=greeting_type, greeting_recording_id=recording_id
    )
    assert validate_greeting_recordings(definition) == []


@pytest.mark.parametrize(
    "definition",
    [
        None,
        {},
        {"nodes": None},
        {"nodes": {}},
        {"nodes": "invalid"},
        {
            "nodes": [
                None,
                [],
                {},
                {"type": "startCall"},
                {"type": "startCall", "data": None},
                {"type": "startCall", "data": []},
                {"type": "startCall", "data": "invalid"},
                {"type": "agentNode", "data": {"greeting_type": "audio"}},
            ]
        },
    ],
)
def test_greeting_authoring_validation_ignores_malformed_node_structures(definition):
    assert validate_greeting_recordings(definition) == []


@pytest.mark.parametrize("recording_id", [0, False, [], {}, ["42"], 42])
def test_greeting_authoring_validation_leaves_invalid_id_types_to_schema(recording_id):
    definition = json.loads((_FIXTURES_DIR / "clean.json").read_text())
    definition["nodes"][0]["data"].update(
        greeting_type="audio", greeting_recording_id=recording_id
    )
    with pytest.raises(ValidationError):
        ReactFlowDTO.model_validate(definition)
    assert validate_greeting_recordings(definition) == []


@pytest.mark.parametrize("node_id", [None, 42])
def test_greeting_authoring_validation_handles_invalid_node_ids(node_id):
    definition = {
        "nodes": [
            {"id": node_id, "type": "startCall", "data": {"greeting_type": "audio"}}
        ]
    }
    errors = validate_greeting_recordings(definition)
    assert len(errors) == 1
    assert errors[0]["id"] is None
    assert errors[0]["field"] == "data.greeting_recording_id"


@pytest.mark.parametrize("greeting_type", [None, "text"])
def test_text_or_default_greeting_does_not_require_a_recording(greeting_type):
    data = StartCallNodeData(name="Start", greeting_type=greeting_type)
    assert data.greeting_recording_id is None


def test_pre_recorded_greeting_accepts_a_selected_recording():
    data = StartCallNodeData(
        name="Start", greeting_type="audio", greeting_recording_id="42"
    )
    assert data.greeting_recording_id == "42"


@pytest.mark.asyncio
async def test_dto():
    # Path resolved relative to this test file so the test works regardless
    # of the cwd pytest is invoked from.
    with open(_FIXTURES_DIR / "sample_branching_workflow.json", "r") as f:
        dto = ReactFlowDTO.model_validate_json(f.read())
    assert dto is not None


def test_dto_ignores_legacy_unknown_node_data_fields():
    dto = ReactFlowDTO.model_validate(
        {
            "nodes": [
                {
                    "id": "n1",
                    "type": "startCall",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "name": "Start",
                        "prompt": "Hello",
                        "is_static": True,
                        "detect_voicemail": True,
                        "wait_for_user_response": False,
                        "wait_for_user_response_timeout": 2.5,
                        "legacy_field": "ignored",
                    },
                }
            ],
            "edges": [],
        }
    )

    data = dto.nodes[0].data.model_dump()
    assert "is_static" not in data
    assert "detect_voicemail" not in data
    assert "wait_for_user_response" not in data
    assert "wait_for_user_response_timeout" not in data
    assert "legacy_field" not in data


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
