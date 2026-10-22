export interface LayoutNode {
    id: string;
    type: string;
}

export interface LayoutEdge {
    source: string;
    target: string;
}

/** The same pure layout used by UI Tidy Up and initial agent creation. */
export function layoutWorkflowNodes<Node extends LayoutNode>(
    nodes: Node[],
    edges: LayoutEdge[],
    rankdir?: 'TB' | 'LR',
): Node[];
