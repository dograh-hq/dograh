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
    g.setGraph({ rankdir, nodesep: 250, ranksep: 250 });
    g.setDefaultEdgeLabel(() => ({}));

    // Sort nodes so startCall nodes come first and endCall nodes come last
    const sortedNodes = [...nodes].sort((a, b) => {
        if (a.type === 'startCall') return -1;
        if (b.type === 'startCall') return 1;
        if (a.type === 'endCall') return 1;
        if (b.type === 'endCall') return -1;
        return 0;
    });

    sortedNodes.forEach((node) => {
        g.setNode(node.id, { width: 180, height: 60 });
    });

    edges.forEach((edge) => {
        g.setEdge(edge.source, edge.target);
    });

    dagre.layout(g);

    const newNodes = sortedNodes.map((node) => {
        const nodeWithPosition = g.node(node.id);
        return {
            ...node,
            position: { x: nodeWithPosition.x, y: nodeWithPosition.y }
        };
    });

    // Fit view to the new layout and save the viewport position
    setTimeout(() => {
        rfInstance.current?.fitView({ padding: 0.2, duration: 200, maxZoom: 0.75 });
    }, 0);

    return newNodes;
};
