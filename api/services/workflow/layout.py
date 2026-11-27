"""Canvas positions for workflows the API writes or lays out.

`save_workflow` re-parses LLM-authored TypeScript into workflow JSON,
but the parser deliberately ignores positions (LLMs place nodes
poorly, and the authoring surface stays tighter without coordinates).
This module fills them back in by matching the newly-parsed nodes
against the previously-stored workflow:

    1. Named match:   (type, data.name) — most reliable
    2. Unnamed match: (type, nth-occurrence-in-order) — best effort
    3. New nodes:     placed adjacent to their first incoming neighbor
                      (src.x + 400, src.y + 200), or (0, 0) if orphan

Initial creation and the UI's Tidy Up button both use the dagre layout in
`api/mcp_server/ts_validator/src/layout.ts` (`tidy_positions` below for Tidy
Up; `parse_code(tidy=True)` for creation). Subsequent saves use this
reconciliation instead, preserving existing positions (including an
intentional origin).
"""

from __future__ import annotations

from typing import Any

_DEFAULT_POSITION: dict[str, float] = {"x": 0.0, "y": 0.0}
# Horizontal / vertical offset for newly-introduced nodes relative to
# their first incoming neighbor. Chosen to roughly match the UI layout's
# node spacing without overlapping the neighbor's card.
_NEW_NODE_DX: float = 400.0
_NEW_NODE_DY: float = 200.0


async def tidy_positions(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    rankdir: str = "TB",
) -> list[dict[str, Any]]:
    """Tidy Up positions (`[{"id", "x", "y"}]`) for an unsaved canvas.

    Only node ids/types and edge endpoints reach the validator, so any
    half-edited canvas state can be laid out. Empty when nothing to lay
    out. Raises `TsBridgeError` if the validator is unavailable.
    """
    # Loading the MCP package registers tools that also import this module.
    from api.mcp_server.ts_bridge import layout_nodes

    return await layout_nodes(
        [{"id": n["id"], "type": n["type"]} for n in nodes],
        [{"source": e["source"], "target": e["target"]} for e in edges],
        rankdir=rankdir,
    )


def reconcile_positions(
    new_wf: dict[str, Any],
    previous_wf: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return `new_wf` with positions filled from `previous_wf` where
    node identity matches, and approximate positions for genuinely new
    nodes. Mutates and returns the same dict (callers typically want
    the mutation)."""
    if not previous_wf:
        _place_new_nodes(new_wf)
        return new_wf

    prev_nodes = previous_wf.get("nodes") or []
    named_positions: dict[tuple[str, str], dict[str, float]] = {}
    unnamed_positions: dict[str, list[dict[str, float]]] = {}

    for n in prev_nodes:
        t = n.get("type") or ""
        name = ((n.get("data") or {}).get("name") or "").strip()
        pos = n.get("position") or dict(_DEFAULT_POSITION)
        if name:
            named_positions[(t, name)] = pos
        else:
            unnamed_positions.setdefault(t, []).append(pos)

    unnamed_cursor: dict[str, int] = {}
    matched_node_ids: set[str] = set()

    for node in new_wf.get("nodes") or []:
        t = node.get("type") or ""
        name = ((node.get("data") or {}).get("name") or "").strip()

        pos: dict[str, float] | None = None
        if name:
            pos = named_positions.get((t, name))
        if pos is None:
            idx = unnamed_cursor.get(t, 0)
            positions = unnamed_positions.get(t, [])
            if idx < len(positions):
                pos = positions[idx]
                unnamed_cursor[t] = idx + 1
        if pos is not None:
            node["position"] = dict(pos)
            matched_node_ids.add(node["id"])

    _place_new_nodes(new_wf, matched_node_ids)
    return new_wf


def _place_new_nodes(
    wf: dict[str, Any], matched_node_ids: set[str] | None = None
) -> None:
    """For unmatched nodes still at (0, 0), pick a position adjacent
    to the first incoming neighbor.
    Runs after named/unnamed matching so only genuinely-new nodes are
    affected."""
    nodes = wf.get("nodes") or []
    if not nodes:
        return
    id_to_node = {n["id"]: n for n in nodes}
    edges = wf.get("edges") or []

    for node in nodes:
        if matched_node_ids and node["id"] in matched_node_ids:
            continue
        pos = node.get("position") or {}
        if pos.get("x") or pos.get("y"):
            continue  # already has a non-origin position
        src_id = next(
            (e["source"] for e in edges if e.get("target") == node["id"]),
            None,
        )
        if src_id and src_id in id_to_node:
            src_pos = id_to_node[src_id].get("position") or dict(_DEFAULT_POSITION)
            node["position"] = {
                "x": float(src_pos.get("x", 0.0)) + _NEW_NODE_DX,
                "y": float(src_pos.get("y", 0.0)) + _NEW_NODE_DY,
            }
        # Leaves truly orphan new nodes at (0, 0). The UI's re-layout
        # pass will pull them into the graph on next edit.
