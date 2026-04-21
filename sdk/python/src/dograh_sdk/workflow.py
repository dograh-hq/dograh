"""Workflow builder.

Users compose workflows by calling `Workflow.add(type="agentNode", ...)`
and `Workflow.edge(source, target, ...)`. Every call is validated
immediately against the spec catalog fetched from the backend, so LLM
hallucinations fail at the call site rather than at save time.

Wire format matches `ReactFlowDTO` from `api/services/workflow/dto.py`
1:1, so `Workflow.to_json()` output can be round-tripped through
`ReactFlowDTO.model_validate` without further translation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ._validation import validate_node_data

if TYPE_CHECKING:
    from ._generated_models import NodeSpec
    from .client import DograhClient
    from .typed._base import TypedNode


@dataclass
class _Node:
    id: str
    type: str
    position: dict[str, float]
    data: dict[str, Any]


@dataclass
class _Edge:
    id: str
    source: str
    target: str
    data: dict[str, Any]


@dataclass
class NodeRef:
    """Opaque handle returned by `Workflow.add()`. Passed to `edge()` to
    wire nodes together without relying on string IDs."""

    id: str
    type: str


class Workflow:
    """Typed builder that produces `ReactFlowDTO`-compatible JSON.

    Usage:
        wf = Workflow(client=client, name="loan_qual")
        start = wf.add(type="startCall", name="greeting", prompt="...")
        qualify = wf.add(type="agentNode", name="qualify", prompt="...")
        wf.edge(start, qualify, label="interested", condition="...")
        payload = wf.to_json()
    """

    def __init__(self, *, client: DograhClient, name: str = "", description: str = ""):
        self._client = client
        self.name = name
        self.description = description
        self._nodes: list[_Node] = []
        self._edges: list[_Edge] = []
        # Auto-incrementing IDs match the pattern used by the existing UI.
        self._next_node_id = 1

    # ── node construction ──────────────────────────────────────────

    def add(
        self,
        *,
        type: str,
        position: tuple[float, float] | None = None,
        **kwargs: Any,
    ) -> NodeRef:
        """Add a node of the given type.

        `type` is a spec name (e.g., "startCall", "agentNode"). Remaining
        kwargs are validated against the spec — unknown or missing
        required fields raise `ValidationError` immediately.

        `position` is optional (x, y) on the React-Flow canvas; omit for
        auto-placement at origin.
        """
        spec: NodeSpec = self._client.get_node_type(type)
        data = validate_node_data(spec.model_dump(mode="json"), kwargs)

        node_id = str(self._next_node_id)
        self._next_node_id += 1
        x, y = position if position is not None else (0.0, 0.0)
        self._nodes.append(
            _Node(
                id=node_id,
                type=type,
                position={"x": float(x), "y": float(y)},
                data=data,
            )
        )
        return NodeRef(id=node_id, type=type)

    def add_typed(
        self,
        node: "TypedNode",
        *,
        position: tuple[float, float] | None = None,
    ) -> NodeRef:
        """Typed variant of `add()` — takes a generated dataclass from
        `dograh_sdk.typed` instead of string+kwargs.

        Equivalent to:
            wf.add(type=node.type, position=..., **node.to_dict())

        Benefits: mypy/pyright catches misspelled fields at edit time,
        and IDEs show field-level docstrings on hover.
        """
        return self.add(type=node.type, position=position, **node.to_dict())

    # ── edge construction ──────────────────────────────────────────

    def edge(
        self,
        source: NodeRef,
        target: NodeRef,
        *,
        label: str,
        condition: str,
        transition_speech: str | None = None,
        transition_speech_type: str | None = None,
        transition_speech_recording_id: str | None = None,
    ) -> None:
        """Connect two nodes with a labeled transition.

        `label` identifies the branch in call logs and LLM tool schemas;
        `condition` is the natural-language predicate the engine evaluates
        to decide when to follow the edge.
        """
        if not label or not label.strip():
            from .errors import ValidationError

            raise ValidationError("edge.label is required")
        if not condition or not condition.strip():
            from .errors import ValidationError

            raise ValidationError("edge.condition is required")

        data: dict[str, Any] = {"label": label, "condition": condition}
        if transition_speech is not None:
            data["transition_speech"] = transition_speech
        if transition_speech_type is not None:
            data["transition_speech_type"] = transition_speech_type
        if transition_speech_recording_id is not None:
            data["transition_speech_recording_id"] = transition_speech_recording_id

        edge_id = f"{source.id}-{target.id}"
        self._edges.append(
            _Edge(id=edge_id, source=source.id, target=target.id, data=data)
        )

    # ── serialization ──────────────────────────────────────────────

    def to_json(self) -> dict[str, Any]:
        """Serialize to the `ReactFlowDTO` wire format.

        Passes directly through `ReactFlowDTO.model_validate` and the
        `WorkflowGraph` constructor — no translation layer needed.
        """
        return {
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type,
                    "position": n.position,
                    "data": n.data,
                }
                for n in self._nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "source": e.source,
                    "target": e.target,
                    "data": e.data,
                }
                for e in self._edges
            ],
            "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
        }

    @classmethod
    def from_json(
        cls,
        data: dict[str, Any],
        *,
        client: DograhClient,
        name: str = "",
    ) -> Workflow:
        """Rebuild a Workflow from a stored `workflow_json` payload.

        Useful for the MCP edit flow: fetch existing workflow, convert to
        SDK objects, let the LLM mutate in code, serialize back.
        """
        wf = cls(client=client, name=name)
        # Rebuild nodes in the same order, preserving IDs.
        for raw in data.get("nodes", []):
            node_id = str(raw.get("id"))
            spec: NodeSpec = client.get_node_type(raw["type"])
            validated = validate_node_data(spec.model_dump(mode="json"), raw.get("data") or {})
            wf._nodes.append(
                _Node(
                    id=node_id,
                    type=raw["type"],
                    position=raw.get("position") or {"x": 0.0, "y": 0.0},
                    data=validated,
                )
            )
        # Keep ID generator above the highest numeric ID seen so new
        # nodes don't collide with existing ones.
        numeric_ids = [int(n.id) for n in wf._nodes if n.id.isdigit()]
        wf._next_node_id = max(numeric_ids, default=0) + 1

        for raw in data.get("edges", []):
            wf._edges.append(
                _Edge(
                    id=str(raw.get("id") or f"{raw['source']}-{raw['target']}"),
                    source=str(raw["source"]),
                    target=str(raw["target"]),
                    data=raw.get("data") or {},
                )
            )
        return wf

    def find_node(self, predicate_or_id: Any) -> NodeRef | None:
        """Lookup a NodeRef by node id or custom predicate. Handy after
        `from_json` when the LLM needs to reference an existing node."""
        if callable(predicate_or_id):
            for n in self._nodes:
                if predicate_or_id(n):
                    return NodeRef(id=n.id, type=n.type)
            return None
        for n in self._nodes:
            if n.id == str(predicate_or_id):
                return NodeRef(id=n.id, type=n.type)
        return None
