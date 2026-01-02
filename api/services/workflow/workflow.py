import re
from collections import Counter
from typing import Dict, List

from api.services.workflow.dto import EdgeDataDTO, NodeDataDTO, NodeType, ReactFlowDTO
from api.services.workflow.errors import ItemKind, WorkflowError


class Edge:
    def __init__(self, source: str, target: str, data: EdgeDataDTO):
        self.source = source
        self.target = target

        self.label = data.label
        self.condition = data.condition

        self.data = data

    def get_function_name(self):
        return re.sub(r"[^a-z0-9]", "_", self.label.lower())

    def __eq__(self, other):
        if not isinstance(other, Edge):
            return False
        return self.source == other.source and self.target == other.target

    def __hash__(self):
        return hash((self.source, self.target))


class Node:
    def __init__(self, id: str, node_type: NodeType, data: NodeDataDTO):
        self.id, self.node_type, self.data = id, node_type, data
        self.out: Dict[str, "Node"] = {}  # forward nodes
        self.out_edges: List[Edge] = []  # forward edges with properties

        self.name = data.name
        self.prompt = data.prompt
        self.is_static = data.is_static
        self.is_start = data.is_start
        self.is_end = data.is_end
        self.allow_interrupt = data.allow_interrupt
        self.extraction_enabled = data.extraction_enabled
        self.extraction_prompt = data.extraction_prompt
        self.extraction_variables = data.extraction_variables
        self.add_global_prompt = data.add_global_prompt
        self.detect_voicemail = data.detect_voicemail
        self.delayed_start = data.delayed_start
        self.delayed_start_duration = data.delayed_start_duration
        self.tool_uuids = data.tool_uuids

        self.data = data


class WorkflowGraph:
    """
    *All* business invariants (acyclic, cardinality, etc.) are verified here.
    The constructor accepts a validated ReactFlowDTO.
    """

    def __init__(self, dto: ReactFlowDTO):
        # build adjacency list
        self.nodes: Dict[str, Node] = {
            n.id: Node(n.id, n.type, n.data) for n in dto.nodes
        }

        # Store all edges
        self.edges: List[Edge] = []

        for e in dto.edges:
            source_node = self.nodes[e.source]
            target_node = self.nodes[e.target]

            # Create the edge with properties from dto
            edge = Edge(source=e.source, target=e.target, data=e.data)

            # Add to the edge list
            self.edges.append(edge)

            # Add to the source node's outgoing edges
            source_node.out_edges.append(edge)

            # Set up the node references for backward compatibility
            source_node.out[target_node.id] = target_node

        self._validate_graph()

        # Get a reference to the start node
        self.start_node_id = [n.id for n in dto.nodes if n.data.is_start][0]

        # Get a reference to the global node
        try:
            self.global_node_id = [
                n.id for n in dto.nodes if n.type == NodeType.globalNode
            ][0]
        except IndexError:
            self.global_node_id = None

    # -----------------------------------------------------------
    # validators
    # -----------------------------------------------------------
    def _validate_graph(self) -> None:
        errors: list[WorkflowError] = []

        # TODO: Figure out what kind of cyclic contraints can be applied, since there can be a cycle in the graph
        # try:
        #     self._assert_acyclic()
        # except ValueError as e:
        #     errors.append(
        #         WorkflowError(
        #             kind=ItemKind.workflow, id=None, field=None, message=str(e)
        #         )
        #     )

        errors.extend(self._assert_start_node())
        errors.extend(self._assert_end_node())
        errors.extend(self._assert_connection_counts())
        errors.extend(self._assert_global_node())
        errors.extend(self._assert_node_configs())
        if errors:
            raise ValueError(errors)

    def _assert_acyclic(self):
        color: Dict[str, str] = {}  # white / gray / black

        def dfs(n: Node):
            if color.get(n.id) == "gray":  # back-edge
                raise ValueError("workflow contains a cycle")
            if color.get(n.id) != "black":
                color[n.id] = "gray"
                for m in n.out.values():
                    dfs(m)
                color[n.id] = "black"

        for n in self.nodes.values():
            dfs(n)

    def _assert_start_node(self):
        errors: list[WorkflowError] = []
        start_node = [n for n in self.nodes.values() if n.data.is_start]
        if not start_node:
            errors.append(
                WorkflowError(
                    kind=ItemKind.workflow,
                    id=None,
                    field=None,
                    message="Workflow must have exactly one start node",
                )
            )
        elif len(start_node) > 1:
            errors.append(
                WorkflowError(
                    kind=ItemKind.workflow,
                    id=None,
                    field=None,
                    message="Workflow must have exactly one start node",
                )
            )
        return errors

    def _assert_global_node(self):
        errors: list[WorkflowError] = []
        global_node = [
            n for n in self.nodes.values() if n.node_type == NodeType.globalNode
        ]
        if not len(global_node) <= 1:
            errors.append(
                WorkflowError(
                    kind=ItemKind.workflow,
                    id=None,
                    field=None,
                    message="Workflow must have at most one global node",
                )
            )
        return errors

    def _assert_end_node(self):
        errors: list[WorkflowError] = []
        end_node = [n for n in self.nodes.values() if n.data.is_end]
        if not end_node:
            errors.append(
                WorkflowError(
                    kind=ItemKind.workflow,
                    id=None,
                    field=None,
                    message="Workflow must have exactly one end node",
                )
            )

        elif len(end_node) > 1:
            errors.append(
                WorkflowError(
                    kind=ItemKind.workflow,
                    id=None,
                    field=None,
                    message="Workflow must have exactly one end node",
                )
            )
        return errors

    def _assert_connection_counts(self):
        errors: list[WorkflowError] = []

        out_deg = Counter()
        in_deg = Counter()
        for n in self.nodes.values():  # init counters
            out_deg[n.id] = in_deg[n.id] = 0
        for src, n in self.nodes.items():  # compute degrees
            for m in n.out.values():
                out_deg[src] += 1
                in_deg[m.id] += 1

        for n in self.nodes.values():
            in_d, out_d = in_deg[n.id], out_deg[n.id]

            match n.node_type:
                case NodeType.startNode:
                    if in_d != 0 or out_d < 1:
                        errors.append(
                            WorkflowError(
                                kind=ItemKind.node,
                                id=n.id,
                                field=None,
                                message=f"StartNode must have at least 1 outgoing edge",
                            )
                        )
                case NodeType.endNode:
                    if in_d < 1 or out_d != 0:
                        errors.append(
                            WorkflowError(
                                kind=ItemKind.node,
                                id=n.id,
                                field=None,
                                message=f"EndNode must have at least 1 incoming edge",
                            )
                        )
                case NodeType.agentNode:
                    if in_d < 1 or out_d < 1:
                        errors.append(
                            WorkflowError(
                                kind=ItemKind.node,
                                id=n.id,
                                field=None,
                                message=f"Worker must have at least 1 incoming and 1 outgoing edge",
                            )
                        )

        return errors

    def _assert_node_configs(self):
        """Validate node-specific configuration constraints."""
        errors: list[WorkflowError] = []

        for node in self.nodes.values():
            # Validate StartNode constraints
            if node.node_type == NodeType.startNode:
                # No specific validations for start node at this time
                pass

        return errors
