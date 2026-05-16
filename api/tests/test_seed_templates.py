"""Tests for built-in workflow templates seeded into ``workflow_templates``.

The seed migration must produce templates that:
  - Validate as ``ReactFlowDTO`` (so the UI/API will accept them)
  - Build a legal ``WorkflowGraph`` (so they actually run end-to-end)
  - Have at least one start node and one end node
  - Carry non-empty name / description so the picker shows something useful

If any of these fail, the seed migration would create a workflow no one
can run — a worse experience than having no template at all.
"""

from __future__ import annotations

import pytest

from api.services.workflow.dto import NodeType, ReactFlowDTO
from api.services.workflow.seed_templates import (
    BUILTIN_TEMPLATES,
    lead_qualification_template,
)
from api.services.workflow.workflow_graph import WorkflowGraph


@pytest.mark.parametrize(
    "tmpl",
    BUILTIN_TEMPLATES,
    ids=lambda t: t["template_name"],
)
def test_builtin_template_validates_against_dto(tmpl):
    """Each seeded template must validate as ReactFlowDTO without errors."""
    ReactFlowDTO.model_validate(tmpl["template_json"])


@pytest.mark.parametrize(
    "tmpl",
    BUILTIN_TEMPLATES,
    ids=lambda t: t["template_name"],
)
def test_builtin_template_builds_workflow_graph(tmpl):
    """Each seeded template must satisfy WorkflowGraph's invariants."""
    dto = ReactFlowDTO.model_validate(tmpl["template_json"])
    graph = WorkflowGraph(dto)
    assert graph.start_node_id is not None


@pytest.mark.parametrize(
    "tmpl",
    BUILTIN_TEMPLATES,
    ids=lambda t: t["template_name"],
)
def test_builtin_template_has_name_and_description(tmpl):
    assert tmpl["template_name"].strip()
    assert len(tmpl["template_description"].strip()) >= 30, (
        "Template description should describe the flow to users picking it."
    )


@pytest.mark.parametrize(
    "tmpl",
    BUILTIN_TEMPLATES,
    ids=lambda t: t["template_name"],
)
def test_builtin_template_has_start_and_end(tmpl):
    nodes = tmpl["template_json"]["nodes"]
    types = [n["type"] for n in nodes]
    assert NodeType.startNode.value in types, "missing startCall node"
    assert NodeType.endNode.value in types, "missing endCall node"


def test_lead_qualification_has_qualified_and_unqualified_branches():
    """The lead-qualification template should fan out from the qualification
    step to at least two end states — one for qualified handoff and one for
    a polite close. Otherwise it's not really a qualification flow."""
    tmpl = lead_qualification_template()

    # Find the qualification agent node (only agentNode in this template).
    agent_ids = [
        n["id"] for n in tmpl["nodes"] if n["type"] == NodeType.agentNode.value
    ]
    assert agent_ids, "lead qualification template must have an agent node"
    qualify_id = agent_ids[0]

    outgoing = [e for e in tmpl["edges"] if e["source"] == qualify_id]
    assert len(outgoing) >= 2, (
        "Qualification node should branch to at least qualified and "
        "unqualified outcomes."
    )

    targets = {e["target"] for e in outgoing}
    end_ids = {
        n["id"]
        for n in tmpl["nodes"]
        if n["type"] == NodeType.endNode.value and n["id"] in targets
    }
    assert len(end_ids) >= 2, (
        "Each qualification branch should resolve to a distinct endCall node."
    )
