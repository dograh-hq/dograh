"""Tests for the Python runtime SDK (`dograh_sdk`).

Uses a stub client backed by the in-process spec registry rather than
exercising the HTTP layer — the HTTP client is a thin wrapper that's
easier to test manually against a live server.

Covers:
- Workflow builder round-trips through ReactFlowDTO validation
- Validation errors fail at the `add()` call site
- from_json preserves node IDs and subsequent add() doesn't collide
- Edge labels / conditions are required
"""

from __future__ import annotations

from typing import Any

import pytest

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.node_specs import all_specs, get_spec
from dograh_sdk import Workflow
from dograh_sdk.errors import ValidationError


class _StubClient:
    """Stand-in for DograhClient backed by the in-process spec registry.
    Matches the shape the SDK needs — `get_node_type(name)` returning a
    JSON-serializable dict."""

    def get_node_type(self, name: str) -> dict[str, Any]:
        spec = get_spec(name)
        if spec is None:
            raise ValueError(f"Unknown spec: {name}")
        return spec.model_dump(mode="json")


@pytest.fixture
def client() -> _StubClient:
    return _StubClient()


# ─── Builder + to_json round-trip ────────────────────────────────────────


def test_builds_minimal_workflow_and_roundtrips_through_dto(client: _StubClient):
    wf = Workflow(client=client, name="minimal")
    start = wf.add(
        type="startCall",
        name="greeting",
        prompt="Say hi to the caller.",
    )
    end = wf.add(
        type="endCall",
        name="close",
        prompt="Thank the caller and hang up.",
    )
    wf.edge(start, end, label="done", condition="When the greeting is complete")

    payload = wf.to_json()
    # Wire format must validate through the backend Pydantic union — if
    # it doesn't, the SDK has silently drifted from the spec schema.
    dto = ReactFlowDTO.model_validate(payload)
    assert len(dto.nodes) == 2
    assert {n.type for n in dto.nodes} == {"startCall", "endCall"}
    assert len(dto.edges) == 1


def test_defaults_applied_from_spec(client: _StubClient):
    """Spec defaults (e.g., `allow_interrupt=False` on startCall) fill in
    when the user doesn't pass them."""
    wf = Workflow(client=client, name="defaults")
    start = wf.add(type="startCall", name="greeting", prompt="hello")
    payload = wf.to_json()
    data = payload["nodes"][0]["data"]
    assert data["allow_interrupt"] is False  # spec default
    assert data["add_global_prompt"] is True  # spec default
    _ = start  # used implicitly; silence unused


def test_webhook_complex_fields_validate(client: _StubClient):
    """Webhook's json + fixed_collection (custom_headers) round-trip."""
    wf = Workflow(client=client, name="wh")
    wh = wf.add(
        type="webhook",
        name="notify",
        enabled=True,
        http_method="POST",
        endpoint_url="https://api.example.com/hook",
        custom_headers=[{"key": "X-Source", "value": "dograh"}],
        payload_template={"run": "{{workflow_run_id}}"},
    )
    payload = wf.to_json()
    # Webhook has no incoming/outgoing graph requirements — render as a
    # standalone node in the graph for the DTO round-trip.
    ReactFlowDTO.model_validate(payload)
    _ = wh


# ─── Validation errors at call site ──────────────────────────────────────


def test_unknown_field_raises_at_add(client: _StubClient):
    wf = Workflow(client=client, name="typo")
    with pytest.raises(ValidationError, match="unknown field"):
        wf.add(
            type="startCall",
            name="greeting",
            prompt="hi",
            promt="typo",  # extra misspelled field
        )


def test_missing_required_raises_at_add(client: _StubClient):
    wf = Workflow(client=client, name="missing")
    with pytest.raises(ValidationError, match="required field missing"):
        wf.add(type="startCall", name="greeting")  # no prompt


def test_wrong_scalar_type_raises(client: _StubClient):
    wf = Workflow(client=client, name="wrongtype")
    with pytest.raises(ValidationError, match="expected boolean"):
        wf.add(
            type="agentNode",
            name="x",
            prompt="y",
            allow_interrupt="yes",
        )


def test_invalid_options_value_raises(client: _StubClient):
    wf = Workflow(client=client, name="wrongenum")
    with pytest.raises(ValidationError, match="not in allowed"):
        wf.add(
            type="startCall",
            name="greeting",
            prompt="hi",
            greeting_type="video",  # only text|audio allowed
        )


def test_unknown_node_type_raises(client: _StubClient):
    wf = Workflow(client=client, name="x")
    with pytest.raises(ValueError, match="Unknown spec"):
        wf.add(type="nonExistentType", name="x")


def test_validation_error_surfaces_llm_hint(client: _StubClient):
    """When a property carries `llm_hint`, it appears in the error message
    so LLMs can self-correct on retry. `tool_uuids` on agentNode has the
    hint 'List of tool UUIDs from `list_tools`.'"""
    wf = Workflow(client=client, name="hint")
    with pytest.raises(ValidationError) as exc_info:
        wf.add(
            type="agentNode",
            name="x",
            prompt="y",
            tool_uuids="single-uuid-not-a-list",  # wrong shape: str, not list
        )
    msg = str(exc_info.value)
    assert "tool_uuids" in msg
    assert "Hint:" in msg
    assert "list_tools" in msg


def test_no_hint_message_when_spec_has_none(client: _StubClient):
    """Properties without `llm_hint` produce a plain error (no dangling
    'Hint:' line)."""
    wf = Workflow(client=client, name="no-hint")
    with pytest.raises(ValidationError) as exc_info:
        wf.add(type="agentNode", name="x", prompt="y", allow_interrupt="yes")
    assert "Hint:" not in str(exc_info.value)


def test_edge_requires_label_and_condition(client: _StubClient):
    wf = Workflow(client=client, name="edge")
    a = wf.add(type="startCall", name="a", prompt="hi")
    b = wf.add(type="endCall", name="b", prompt="bye")
    with pytest.raises(ValidationError, match="label is required"):
        wf.edge(a, b, label="", condition="condition")
    with pytest.raises(ValidationError, match="condition is required"):
        wf.edge(a, b, label="label", condition="")


# ─── Round-trip from_json → edit → to_json ────────────────────────────────


def test_from_json_preserves_ids_and_next_id_doesnt_collide(client: _StubClient):
    wf0 = Workflow(client=client, name="w0")
    start = wf0.add(type="startCall", name="g", prompt="hi")
    end = wf0.add(type="endCall", name="e", prompt="bye")
    wf0.edge(start, end, label="done", condition="done")

    payload = wf0.to_json()
    wf1 = Workflow.from_json(payload, client=client, name="w0-reload")

    # IDs are preserved
    assert [n.id for n in wf1._nodes] == [start.id, end.id]
    # Next add() gets a fresh ID, not colliding with the existing ones
    new_ref = wf1.add(type="agentNode", name="qualify", prompt="ask stuff")
    assert new_ref.id != start.id
    assert new_ref.id != end.id
    assert int(new_ref.id) > max(int(start.id), int(end.id))


def test_from_json_validates_data(client: _StubClient):
    """Loading a JSON payload with a misnamed field raises — we don't
    silently accept drift."""
    bad = {
        "nodes": [
            {
                "id": "1",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {"name": "g", "prompt": "hi", "bogus": 1},
            }
        ],
        "edges": [],
    }
    with pytest.raises(ValidationError, match="unknown field"):
        Workflow.from_json(bad, client=client)


# ─── Sanity: all registered specs are reachable by name ───────────────────


def test_every_registered_spec_is_reachable_by_sdk(client: _StubClient):
    wf = Workflow(client=client, name="probe")
    for spec in all_specs():
        # Just fetch the spec via the client; doesn't add anything. This
        # ensures the `_StubClient` wiring works for all types.
        probe = client.get_node_type(spec.name)
        assert probe["name"] == spec.name
    _ = wf
