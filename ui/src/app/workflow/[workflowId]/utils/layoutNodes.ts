import dagre from '@dagrejs/dagre';
import { ReactFlowInstance } from "@xyflow/react";

import { FlowEdge, FlowNode, NodeType } from "@/components/flow/types";

// Node dimensions
const NODE_WIDTH = 350;
const NODE_HEIGHT = 120;
const VERTICAL_SPACING = 150; // Vertical spacing between stacked nodes
const SECTION_HORIZONTAL_GAP = 500; // Horizontal gap between sections

export const layoutNodes = (
    nodes: FlowNode[],
    edges: FlowEdge[],
    rankdir: 'TB' | 'LR',
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>
) => {
    // Separate nodes by type
    const triggerNodes = nodes.filter(n => n.type === NodeType.TRIGGER);
    const webhookNodes = nodes.filter(n => n.type === NodeType.WEBHOOK);
    const globalNodes = nodes.filter(n => n.type === NodeType.GLOBAL_NODE || n.type === 'global');
    const workflowNodes = nodes.filter(n =>
        n.type === NodeType.START_CALL ||
        n.type === NodeType.AGENT_NODE ||
        n.type === NodeType.END_CALL ||
        n.type === 'startCall' ||
        n.type === 'agentNode' ||
        n.type === 'endCall'
    );

    // If no workflow nodes, just return original nodes
    if (workflowNodes.length === 0) {
        return nodes;
    }

    // Layout workflow nodes using dagre
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir, nodesep: 400, ranksep: 300 });
    g.setDefaultEdgeLabel(() => ({}));

    // Sort workflow nodes so startCall comes first and endCall comes last
    const sortedWorkflowNodes = [...workflowNodes].sort((a, b) => {
        if (a.type === 'startCall' || a.type === NodeType.START_CALL) return -1;
        if (b.type === 'startCall' || b.type === NodeType.START_CALL) return 1;
        if (a.type === 'endCall' || a.type === NodeType.END_CALL) return 1;
        if (b.type === 'endCall' || b.type === NodeType.END_CALL) return -1;
        return 0;
    });

    sortedWorkflowNodes.forEach((node) => {
        g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
    });

    // Only include edges between workflow nodes
    const workflowNodeIds = new Set(workflowNodes.map(n => n.id));
    const workflowEdges = edges.filter(e =>
        workflowNodeIds.has(e.source) && workflowNodeIds.has(e.target)
    );

    workflowEdges.forEach((edge) => {
        g.setEdge(edge.source, edge.target);
    });

    dagre.layout(g);

    // Group workflow nodes by their Y position (rank/depth level)
    const nodesByRank = new Map<number, { node: FlowNode; dagreNode: dagre.Node }[]>();
    sortedWorkflowNodes.forEach((node) => {
        const dagreNode = g.node(node.id);
        const rankY = Math.round(dagreNode.y / 50) * 50;
        if (!nodesByRank.has(rankY)) {
            nodesByRank.set(rankY, []);
        }
        nodesByRank.get(rankY)!.push({ node, dagreNode });
    });

    const horizontalStagger = 600;
    const ranks = Array.from(nodesByRank.keys()).sort((a, b) => a - b);

    // Calculate workflow bounds
    let workflowMinX = Infinity;
    let workflowMaxX = -Infinity;
    let workflowMinY = Infinity;
    let workflowMaxY = -Infinity;

    const positionedWorkflowNodes = sortedWorkflowNodes.map((node) => {
        const dagreNode = g.node(node.id);
        const rankY = Math.round(dagreNode.y / 50) * 50;
        const rankIndex = ranks.indexOf(rankY);
        const nodesAtRank = nodesByRank.get(rankY)!;

        let xOffset = 0;

        // Apply zigzag pattern for single nodes at each rank
        if (nodesAtRank.length === 1) {
            if (node.type !== 'startCall' && node.type !== NodeType.START_CALL &&
                node.type !== 'endCall' && node.type !== NodeType.END_CALL) {
                xOffset = (rankIndex % 2 === 0) ? -horizontalStagger : horizontalStagger;
            }
        }

        const x = dagreNode.x + xOffset;
        const y = dagreNode.y;

        workflowMinX = Math.min(workflowMinX, x);
        workflowMaxX = Math.max(workflowMaxX, x + NODE_WIDTH);
        workflowMinY = Math.min(workflowMinY, y);
        workflowMaxY = Math.max(workflowMaxY, y + NODE_HEIGHT);

        return {
            ...node,
            position: { x, y }
        };
    });

    // Calculate center Y of the workflow for vertical alignment
    const workflowCenterY = (workflowMinY + workflowMaxY) / 2;
    const workflowTopY = workflowMinY;

    // Position global nodes to the left of the workflow, close to it
    const globalNodesX = workflowMinX - SECTION_HORIZONTAL_GAP;
    const positionedGlobalNodes = globalNodes.map((node, index) => {
        const totalHeight = globalNodes.length * NODE_HEIGHT + (globalNodes.length - 1) * VERTICAL_SPACING;
        const startY = workflowCenterY - totalHeight / 2;
        return {
            ...node,
            position: {
                x: globalNodesX,
                y: startY + index * (NODE_HEIGHT + VERTICAL_SPACING)
            }
        };
    });

    // Position trigger nodes to the left of global nodes (or workflow if no global)
    const triggerNodesX = globalNodes.length > 0
        ? globalNodesX - SECTION_HORIZONTAL_GAP
        : workflowMinX - SECTION_HORIZONTAL_GAP;
    const positionedTriggerNodes = triggerNodes.map((node, index) => {
        const totalHeight = triggerNodes.length * NODE_HEIGHT + (triggerNodes.length - 1) * VERTICAL_SPACING;
        const startY = workflowTopY + (workflowMaxY - workflowMinY) / 2 - totalHeight / 2;
        return {
            ...node,
            position: {
                x: triggerNodesX,
                y: startY + index * (NODE_HEIGHT + VERTICAL_SPACING)
            }
        };
    });

    // Position webhook nodes to the right of the workflow
    const webhookNodesX = workflowMaxX + SECTION_HORIZONTAL_GAP;
    const positionedWebhookNodes = webhookNodes.map((node, index) => {
        const totalHeight = webhookNodes.length * NODE_HEIGHT + (webhookNodes.length - 1) * VERTICAL_SPACING;
        const startY = workflowCenterY - totalHeight / 2;
        return {
            ...node,
            position: {
                x: webhookNodesX,
                y: startY + index * (NODE_HEIGHT + VERTICAL_SPACING)
            }
        };
    });

    // Combine all positioned nodes
    const allPositionedNodes = [
        ...positionedTriggerNodes,
        ...positionedGlobalNodes,
        ...positionedWorkflowNodes,
        ...positionedWebhookNodes
    ];

    // Create a map for quick lookup
    const positionedNodeMap = new Map(allPositionedNodes.map(n => [n.id, n]));

    // Return nodes in original order but with new positions
    const newNodes = nodes.map(node => positionedNodeMap.get(node.id) || node);

    // Fit view to the new layout
    setTimeout(() => {
        rfInstance.current?.fitView({ padding: 0.2, duration: 200, maxZoom: 0.75 });
    }, 0);

    return newNodes;
};
