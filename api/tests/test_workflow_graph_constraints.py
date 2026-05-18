"""Regression tests for WorkflowGraph edge/graph constraints + the
rule-based audit that mirrors them.

Each fixture in `dto_fixtures/` is either a clean workflow or a single
category of violation we found in production. We pin two layers:

  1. WorkflowGraph — semantic gate used by `/publish`, the SDK API, and
     both MCP tools. Driven by `NodeSpec.graph_constraints`. If this
     layer ever stops rejecting one of these fixtures, the production
     write paths will quietly start accepting bad workflows again.
  2. audit_definition (api.services.workflow.audit) — read-only sweep
     over persisted rows used by the admin cleanup script to find
     legacy/imported breakage. Pinned so refactors of the rule set
     don't silently change the verdicts the migration relies on.

DTO-level shape validation is covered by `test_dto.py` and isn't
re-pinned here.
"""

import json
from pathlib import Path

import pytest

from api.services.workflow.audit import audit_definition
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph

_FIXTURES_DIR = Path(__file__).parent / "dto_fixtures"


def _load(name: str) -> tuple[str, dict]:
    raw = (_FIXTURES_DIR / f"{name}.json").read_text()
    return raw, json.loads(raw)


# (fixture_name, expected_audit_reasons, expected_graph_messages)
#
# expected_graph_messages semantics:
#   None  — DTO rejects upstream, WorkflowGraph is never reached.
#   []    — WorkflowGraph accepts (clean fixture).
#   [...] — WorkflowGraph rejects; each substring must appear in the
#           emitted WorkflowError messages.
_SCENARIOS = [
    ("clean", [], []),
    # A workflow with just a startCall and no edges is valid — startCall
    # has no `min_outgoing` constraint, so a "greet then hang up" flow
    # passes both audit and WorkflowGraph.
    ("start_only", [], []),
    (
        "bad_edge_into_start",
        ["target_max_incoming_0:startCall"],
        ["Start Call cannot have incoming edges"],
    ),
    (
        "bad_edge_into_webhook",
        ["target_max_incoming_0:webhook"],
        ["Webhook cannot have incoming edges"],
    ),
    (
        "bad_edge_out_of_webhook",
        ["source_max_outgoing_0:webhook"],
        ["Webhook cannot have outgoing edges"],
    ),
    (
        "bad_edge_out_of_globalnode",
        ["source_max_outgoing_0:globalNode"],
        ["Global Node cannot have outgoing edges"],
    ),
    ("bad_edge_target_missing", ["target_id_missing"], None),
    ("bad_edge_source_missing", ["source_id_missing"], None),
    (
        "no_start_node",
        ["no_start_node"],
        ["Workflow has no start node"],
    ),
    # Two startCall nodes — surfaced separately from no_start_node so
    # the editor can show a count-specific message.
    (
        "multiple_start_nodes",
        ["multiple_start_nodes:2"],
        ["Workflow has 2 start nodes"],
    ),
]


@pytest.mark.parametrize(
    "name,expected_reasons",
    [(name, reasons) for name, reasons, _ in _SCENARIOS],
)
def test_audit_catches_violations(name, expected_reasons):
    _, definition = _load(name)
    violations = audit_definition(definition["nodes"], definition["edges"])
    reasons = sorted(v["reason"] for v in violations)
    assert reasons == sorted(expected_reasons)


@pytest.mark.parametrize(
    "name,expected_graph_messages",
    [
        (name, messages)
        for name, _, messages in _SCENARIOS
        if messages is not None  # skip fixtures DTO rejects upstream
    ],
)
def test_workflow_graph_rejects_violations(name, expected_graph_messages):
    """If WorkflowGraph accepts a definition, every save path that goes
    'live' will accept it — so this layer is the canonical regression
    point for the rules in `NodeSpec.graph_constraints`."""
    raw, _ = _load(name)
    dto = ReactFlowDTO.model_validate_json(raw)

    if not expected_graph_messages:
        WorkflowGraph(dto)
        return

    with pytest.raises(ValueError) as exc_info:
        WorkflowGraph(dto)

    actual_messages = [w["message"] for w in exc_info.value.args[0]]
    for expected in expected_graph_messages:
        assert any(expected in m for m in actual_messages), (
            f"Expected substring {expected!r} not found in graph errors: {actual_messages}"
        )
