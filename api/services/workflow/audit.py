"""Rule-based audit of a workflow definition's nodes + edges.

Pure, dependency-free helpers derived from `NodeSpec.graph_constraints`.
Lives in tracked code so the regression tests in
`test_workflow_graph_constraints.py` can pin it; the admin cleanup
script in `api/services/admin_utils/local_exec.py` is the production
consumer.
"""

from api.services.workflow.node_specs import REGISTRY


def _build_type_rules() -> tuple[set[str], set[str]]:
    """From NodeSpec.graph_constraints, derive the set of types that are
    forbidden as edge sources (max_outgoing == 0) and as targets
    (max_incoming == 0)."""
    src_forbidden: set[str] = set()
    tgt_forbidden: set[str] = set()
    for name, spec in REGISTRY.items():
        gc = spec.graph_constraints
        if gc is None:
            continue
        if gc.max_outgoing == 0:
            src_forbidden.add(name)
        if gc.max_incoming == 0:
            tgt_forbidden.add(name)
    return src_forbidden, tgt_forbidden


def _empty_violation(reason: str) -> dict:
    """Graph-level violation row — no edge metadata to attach."""
    return {
        "edge_id": "(graph)",
        "source_id": None,
        "source_type": None,
        "target_id": None,
        "target_type": None,
        "edge_label": None,
        "reason": reason,
    }


def audit_definition(nodes, edges) -> list[dict]:
    """Rule-based audit — emits one row per offending edge.

    Used by the cleanup migration which needs per-edge granularity to
    know what to strip. Pinned by tests in test_workflow_graph_constraints.py.
    """
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return []

    src_forbidden, tgt_forbidden = _build_type_rules()
    nodes_by_id: dict = {}
    for n in nodes:
        if isinstance(n, dict) and "id" in n:
            nodes_by_id[n["id"]] = n.get("type")

    violations: list[dict] = []

    # Graph-level: WorkflowGraph._assert_start_node requires exactly one
    # startCall node. The DTO doesn't enforce this, so legacy or
    # script-edited rows can land in a state that fails at runtime.
    start_count = sum(1 for t in nodes_by_id.values() if t == "startCall")
    if start_count == 0:
        violations.append(_empty_violation("no_start_node"))
    elif start_count > 1:
        violations.append(_empty_violation(f"multiple_start_nodes:{start_count}"))
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("source")
        tgt = e.get("target")
        eid = e.get("id") or f"{src}->{tgt}"
        src_type = nodes_by_id.get(src) if src is not None else None
        tgt_type = nodes_by_id.get(tgt) if tgt is not None else None

        reasons: list[str] = []
        if src is None or src not in nodes_by_id:
            reasons.append("source_id_missing")
        if tgt is None or tgt not in nodes_by_id:
            reasons.append("target_id_missing")
        if src_type in src_forbidden:
            reasons.append(f"source_max_outgoing_0:{src_type}")
        if tgt_type in tgt_forbidden:
            reasons.append(f"target_max_incoming_0:{tgt_type}")

        for r in reasons:
            violations.append(
                {
                    "edge_id": eid,
                    "source_id": src,
                    "source_type": src_type,
                    "target_id": tgt,
                    "target_type": tgt_type,
                    "edge_label": (e.get("data") or {}).get("label")
                    if isinstance(e.get("data"), dict)
                    else None,
                    "reason": r,
                }
            )
    return violations
