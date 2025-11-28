import dagre from '@dagrejs/dagre';
import { ReactFlowInstance } from "@xyflow/react";

import { FlowEdge, FlowNode } from "@/components/flow/types";

export const layoutNodes = (
    nodes: FlowNode[],
    edges: FlowEdge[],
    rankdir: 'TB' | 'LR',
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>
) => {
    const g = new dagre.graphlib.Graph();
    // For TB (top-to-bottom) layout:
    // - nodesep: horizontal spacing between nodes at the same depth level
    // - ranksep: vertical spacing between depth levels
    g.setGraph({ rankdir, nodesep: 400, ranksep: 300 });
    g.setDefaultEdgeLabel(() => ({}));

    // Sort nodes so startCall nodes come first and endCall nodes come last
    const sortedNodes = [...nodes].sort((a, b) => {
        if (a.type === 'startCall') return -1;
        if (b.type === 'startCall') return 1;
        if (a.type === 'endCall') return 1;
        if (b.type === 'endCall') return -1;
        return 0;
    });

    // Use larger node dimensions to account for actual rendered size
    // This prevents overlapping when dagre calculates positions
    sortedNodes.forEach((node) => {
        g.setNode(node.id, { width: 350, height: 120 });
    });

    edges.forEach((edge) => {
        g.setEdge(edge.source, edge.target);
    });

    dagre.layout(g);

    // Group nodes by their Y position (rank/depth level)
    const nodesByRank = new Map<number, { node: FlowNode; dagreNode: dagre.Node }[]>();
    sortedNodes.forEach((node) => {
        const dagreNode = g.node(node.id);
        const rankY = Math.round(dagreNode.y / 50) * 50; // Round to group nearby Y values
        if (!nodesByRank.has(rankY)) {
            nodesByRank.set(rankY, []);
        }
        nodesByRank.get(rankY)!.push({ node, dagreNode });
    });

    // Calculate horizontal offset for zigzag pattern
    // Nodes at each rank level get staggered left/right
    const horizontalStagger = 600; // How much to offset alternating ranks
    const ranks = Array.from(nodesByRank.keys()).sort((a, b) => a - b);

    const newNodes = sortedNodes.map((node) => {
        const dagreNode = g.node(node.id);
        const rankY = Math.round(dagreNode.y / 50) * 50;
        const rankIndex = ranks.indexOf(rankY);
        const nodesAtRank = nodesByRank.get(rankY)!;

        let xOffset = 0;

        // Apply zigzag pattern: alternate ranks offset left/right
        // But only if there's a single node at this rank (linear chain)
        if (nodesAtRank.length === 1) {
            // Skip startCall (keep centered) and endCall (keep centered)
            if (node.type !== 'startCall' && node.type !== 'endCall' && node.type !== 'global') {
                xOffset = (rankIndex % 2 === 0) ? -horizontalStagger : horizontalStagger;
            }
        }

        return {
            ...node,
            position: {
                x: dagreNode.x + xOffset,
                y: dagreNode.y
            }
        };
    });

    // Fit view to the new layout and save the viewport position
    setTimeout(() => {
        rfInstance.current?.fitView({ padding: 0.2, duration: 200, maxZoom: 0.75 });
    }, 0);

    return newNodes;
};
