"""Tests for the typed SDK (`dograh_sdk.typed`).

Covers:
- Generated classes import cleanly and declare the correct spec name
- `Workflow.add_typed(node)` produces the same wire format as
  `Workflow.add(type=..., **kwargs)`
- Typed-class construction respects required/optional field defaults
"""

from __future__ import annotations

from typing import Any

import pytest
from dograh_sdk import Workflow
from dograh_sdk._generated_models import NodeSpec
from dograh_sdk.typed import (
    AgentNode,
    EndCall,
    GlobalNode,
    Qa,
    StartCall,
    Trigger,
    TypedNode,
    Webhook,
)

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.node_specs import get_spec


class _StubClient:
    def get_node_type(self, name: str) -> NodeSpec:
        return NodeSpec.model_validate(get_spec(name).model_dump(mode="json"))


@pytest.fixture
def client() -> _StubClient:
    return _StubClient()


# ─── Generated classes declare the correct discriminator ──────────────────


@pytest.mark.parametrize(
    "cls,expected_type",
    [
        (StartCall, "startCall"),
        (AgentNode, "agentNode"),
        (EndCall, "endCall"),
        (GlobalNode, "globalNode"),
        (Trigger, "trigger"),
        (Webhook, "webhook"),
        (Qa, "qa"),
    ],
    ids=lambda v: v.__name__ if isinstance(v, type) else v,
)
def test_typed_class_declares_spec_name(cls: type[TypedNode], expected_type: str):
    assert cls.type == expected_type
    # Instances inherit the ClassVar
    if cls is StartCall:
        inst = cls(name="g", prompt="hi")
    elif cls is AgentNode:
        inst = cls(name="a", prompt="hi")
    elif cls is EndCall:
        inst = cls(name="e", prompt="hi")
    elif cls is GlobalNode:
        inst = cls(name="g", prompt="hi")
    elif cls is Trigger:
        inst = cls(name="t")
    elif cls is Webhook:
        inst = cls(name="wh")
    else:  # Qa
        inst = cls(name="qa")
    assert inst.type == expected_type


# ─── add_typed integrates with Workflow and round-trips through DTO ──────


def test_add_typed_builds_valid_workflow(client: _StubClient):
    wf = Workflow(client=client, name="typed-e2e")
    start = wf.add_typed(StartCall(name="greeting", prompt="Hi there!"))
    end = wf.add_typed(EndCall(name="done", prompt="Bye."))
    wf.edge(start, end, label="done", condition="conversation over")

    payload = wf.to_json()
    dto = ReactFlowDTO.model_validate(payload)
    assert len(dto.nodes) == 2
    assert payload["nodes"][0]["type"] == "startCall"
    assert payload["nodes"][1]["type"] == "endCall"


def test_add_typed_and_add_produce_identical_data(client: _StubClient):
    """The typed path and the generic path should produce identical node
    data for equivalent inputs."""
    wf_typed = Workflow(client=client)
    wf_typed.add_typed(AgentNode(name="q", prompt="ask"))

    wf_generic = Workflow(client=client)
    wf_generic.add(type="agentNode", name="q", prompt="ask")

    typed_data = wf_typed.to_json()["nodes"][0]["data"]
    generic_data = wf_generic.to_json()["nodes"][0]["data"]
    assert typed_data == generic_data


def test_webhook_mutable_defaults_dont_share_state(client: _StubClient):
    """Dataclass default_factory ensures every Webhook() gets its own dict."""
    wf = Workflow(client=client)
    a = wf.add_typed(Webhook(name="a"))
    b = wf.add_typed(Webhook(name="b"))
    payload = wf.to_json()
    a_data = payload["nodes"][0]["data"]
    b_data = payload["nodes"][1]["data"]
    # Both instances must end up with payload_template populated from the
    # factory; mutating one must not affect the other.
    assert a_data["payload_template"] is not b_data["payload_template"]
    _ = a, b


def test_typed_sdk_surfaces_spec_default_to_field(client: _StubClient):
    """Spec defaults make it all the way through: StartCall().name defaults
    to the spec's `"Start Call"` literal."""
    s = StartCall(prompt="hi")
    assert s.name == "Start Call"
    assert s.allow_interrupt is False  # matches spec default from earlier edits
    assert s.add_global_prompt is True
