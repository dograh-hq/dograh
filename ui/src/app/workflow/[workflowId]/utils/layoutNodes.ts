import { ReactFlowInstance } from "@xyflow/react";

import { layoutWorkflowApiV1WorkflowLayoutPost } from '@/client';
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { detailFromError } from '@/lib/apiError';

/**
 * Tidy Up: the API applies the same layout it uses when an agent is first
 * created. Resolves to the input array untouched when there is nothing to
 * lay out; throws with a user-facing message when the request fails.
 */
export const layoutNodes = async (
    nodes: FlowNode[],
    edges: FlowEdge[],
    rankdir: 'TB' | 'LR',
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>
): Promise<FlowNode[]> => {
    const response = await layoutWorkflowApiV1WorkflowLayoutPost({
        body: {
            nodes: nodes.map(({ id, type }) => ({ id, type })),
            edges: edges.map(({ source, target }) => ({ source, target })),
            rankdir,
        },
    });
    if (response.error || !response.data) {
        throw new Error(detailFromError(response.error, 'Could not tidy up the canvas.'));
    }
    const positions = new Map(response.data.positions.map(({ id, x, y }) => [id, { x, y }]));
    if (positions.size === 0) return nodes;

    setTimeout(() => {
        rfInstance.current?.fitView({ padding: 0.2, duration: 200, maxZoom: 0.75 });
    }, 0);
    return nodes.map((node) => {
        const position = positions.get(node.id);
        return position ? { ...node, position } : node;
    });
};
