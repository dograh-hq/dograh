import { layoutWorkflowNodes } from '@dograh/workflow-layout';
import { ReactFlowInstance } from "@xyflow/react";

import { FlowEdge, FlowNode } from "@/components/flow/types";

export const layoutNodes = (
    nodes: FlowNode[],
    edges: FlowEdge[],
    rankdir: 'TB' | 'LR',
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>
) => {
    const positionedNodes = layoutWorkflowNodes(nodes, edges, rankdir);
    if (positionedNodes === nodes) return nodes;

    setTimeout(() => {
        rfInstance.current?.fitView({ padding: 0.2, duration: 200, maxZoom: 0.75 });
    }, 0);
    return positionedNodes;
};
