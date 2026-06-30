"""Rule-based audit of a workflow definition's nodes + edges.

Pure, dependency-free helpers derived from `NodeSpec.graph_constraints`.
Lives in tracked code so the regression tests in
`test_workflow_graph_constraints.py` can pin it; the admin cleanup
script in `api/services/admin_utils/local_exec.py` is the production
consumer.
"""

from collections import Counter

from api.services.workflow.node_specs import all_specs


def _build_type_rules() -> tuple[set[str], set[str], dict[str, int], dict[str, int]]:
    """From NodeSpec.graph_constraints, derive the set of types that are
    forbidden as edge sources (max_outgoing == 0) and as targets
    (max_incoming == 0)."""
    src_forbidden: set[str] = set()
    tgt_forbidden: set[str] = set()
    min_instances: dict[str, int] = {}
    max_instances: dict[str, int] = {}
    for spec in all_specs():
        gc = spec.graph_constraints
        if gc is None:
            continue
        if gc.max_outgoing == 0:
            src_forbidden.add(spec.name)
        if gc.max_incoming == 0:
            tgt_forbidden.add(spec.name)
        if gc.min_instances is not None:
            min_instances[spec.name] = gc.min_instances
        if gc.max_instances is not None:
            max_instances[spec.name] = gc.max_instances
    return src_forbidden, tgt_forbidden, min_instances, max_instances


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

    src_forbidden, tgt_forbidden, min_instances, max_instances = _build_type_rules()
    nodes_by_id: dict = {}
    for n in nodes:
        if isinstance(n, dict) and "id" in n:
            nodes_by_id[n["id"]] = n.get("type")

    violations: list[dict] = []

    node_counts = Counter(t for t in nodes_by_id.values() if isinstance(t, str))
    for node_type, min_count in min_instances.items():
        count = node_counts.get(node_type, 0)
        if count < min_count:
            reason = (
                "no_start_node"
                if node_type == "startCall" and min_count == 1
                else f"min_instances_{min_count}:{node_type}:{count}"
            )
            violations.append(_empty_violation(reason))
    for node_type, max_count in max_instances.items():
        count = node_counts.get(node_type, 0)
        if count > max_count:
            reason = (
                f"multiple_start_nodes:{count}"
                if node_type == "startCall" and max_count == 1
                else f"max_instances_{max_count}:{node_type}:{count}"
            )
            violations.append(_empty_violation(reason))
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
