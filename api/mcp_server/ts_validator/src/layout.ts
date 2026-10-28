// Canvas layout shared by the UI's Tidy Up button (via POST /workflow/layout)
// and initial agent creation (`parse` with `tidy: true`). Conversation nodes
// are ranked with dagre; trigger, global and right-rail nodes are placed in
// columns beside them. Pure: never mutates its inputs.

import dagre from "@dagrejs/dagre";

export interface LayoutNode {
    id: string;
    type: string;
}

export interface LayoutEdge {
    source: string;
    target: string;
}

export interface Position {
    x: number;
    y: number;
}

export type RankDir = "TB" | "LR";

// Node dimensions
const NODE_WIDTH = 350;
const NODE_HEIGHT = 120;
const VERTICAL_SPACING = 150; // Vertical spacing between stacked nodes
const SECTION_HORIZONTAL_GAP = 500; // Horizontal gap between sections
const HORIZONTAL_STAGGER = 600; // Zigzag offset for single-node ranks

const WORKFLOW_NODE_TYPES = new Set(["startCall", "agentNode", "endCall"]);

function isRightRailNode(type: string): boolean {
    if (type === "webhook" || type === "qa") {
        return true;
    }
    return (
        !WORKFLOW_NODE_TYPES.has(type) &&
        type !== "trigger" &&
        type !== "globalNode"
    );
}

/**
 * Returns every node with a fresh position, in the input order, or `null`
 * when there are no conversation nodes to anchor the layout on.
 */
export function layoutWorkflowNodes<N extends LayoutNode>(
    nodes: N[],
    edges: LayoutEdge[],
    rankdir: RankDir = "TB",
): (N & { position: Position })[] | null {
    // Separate nodes by type
    const triggerNodes = nodes.filter((n) => n.type === "trigger");
    const globalNodes = nodes.filter((n) => n.type === "globalNode");
    const workflowNodes = nodes.filter((n) => WORKFLOW_NODE_TYPES.has(n.type));
    const rightSideNodes = nodes.filter((n) => isRightRailNode(n.type));

    if (workflowNodes.length === 0) {
        return null;
    }

    // Layout workflow nodes using dagre
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir, nodesep: 400, ranksep: 300 });
    g.setDefaultEdgeLabel(() => ({}));

    // Sort workflow nodes so startCall comes first and endCall comes last
    const sortedWorkflowNodes = [...workflowNodes].sort((a, b) => {
        if (a.type === "startCall") return -1;
        if (b.type === "startCall") return 1;
        if (a.type === "endCall") return 1;
        if (b.type === "endCall") return -1;
        return 0;
    });

    sortedWorkflowNodes.forEach((node) => {
        g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    });

    // Only include edges between workflow nodes
    const workflowNodeIds = new Set(workflowNodes.map((n) => n.id));
    edges
        .filter((e) => workflowNodeIds.has(e.source) && workflowNodeIds.has(e.target))
        .forEach((edge) => {
            g.setEdge(edge.source, edge.target);
        });

    dagre.layout(g);

    // Group workflow nodes by their Y position (rank/depth level)
    const rankOf = (id: string): number => Math.round(g.node(id).y / 50) * 50;
    const nodesByRank = new Map<number, number>();
    sortedWorkflowNodes.forEach((node) => {
        const rankY = rankOf(node.id);
        nodesByRank.set(rankY, (nodesByRank.get(rankY) ?? 0) + 1);
    });
    const ranks = Array.from(nodesByRank.keys()).sort((a, b) => a - b);

    // Calculate workflow bounds
    let workflowMinX = Infinity;
    let workflowMaxX = -Infinity;
    let workflowMinY = Infinity;
    let workflowMaxY = -Infinity;

    const positionedWorkflowNodes = sortedWorkflowNodes.map((node) => {
        const dagreNode = g.node(node.id);
        const rankY = rankOf(node.id);
        const rankIndex = ranks.indexOf(rankY);

        let xOffset = 0;

        // Apply zigzag pattern for single nodes at each rank
        if (nodesByRank.get(rankY) === 1 && node.type !== "startCall" && node.type !== "endCall") {
            xOffset = rankIndex % 2 === 0 ? -HORIZONTAL_STAGGER : HORIZONTAL_STAGGER;
        }

        const x = dagreNode.x + xOffset;
        const y = dagreNode.y;

        workflowMinX = Math.min(workflowMinX, x);
        workflowMaxX = Math.max(workflowMaxX, x + NODE_WIDTH);
        workflowMinY = Math.min(workflowMinY, y);
        workflowMaxY = Math.max(workflowMaxY, y + NODE_HEIGHT);

        return { ...node, position: { x, y } };
    });

    // Calculate center Y of the workflow for vertical alignment
    const workflowCenterY = (workflowMinY + workflowMaxY) / 2;

    const columnStartY = (count: number): number =>
        workflowCenterY - (count * NODE_HEIGHT + (count - 1) * VERTICAL_SPACING) / 2;
    const placeColumn = (column: N[], x: number): (N & { position: Position })[] => {
        const startY = columnStartY(column.length);
        return column.map((node, index) => ({
            ...node,
            position: { x, y: startY + index * (NODE_HEIGHT + VERTICAL_SPACING) },
        }));
    };

    // Global nodes sit to the left of the workflow; triggers to the left of
    // those (or of the workflow when there are no global nodes).
    const globalNodesX = workflowMinX - SECTION_HORIZONTAL_GAP;
    const triggerNodesX = globalNodes.length > 0
        ? globalNodesX - SECTION_HORIZONTAL_GAP
        : workflowMinX - SECTION_HORIZONTAL_GAP;
    const rightRailX = workflowMaxX + SECTION_HORIZONTAL_GAP;

    const positionedNodeMap = new Map(
        [
            ...placeColumn(triggerNodes, triggerNodesX),
            ...placeColumn(globalNodes, globalNodesX),
            ...positionedWorkflowNodes,
            ...placeColumn(rightSideNodes, rightRailX),
        ].map((n) => [n.id, n]),
    );

    // Return nodes in original order but with new positions
    return nodes.map((node) => positionedNodeMap.get(node.id) ?? { ...node, position: { x: 0, y: 0 } });
}
