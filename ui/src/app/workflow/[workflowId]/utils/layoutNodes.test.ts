import { ReactFlowInstance } from '@xyflow/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { FlowEdge, FlowNode } from '@/components/flow/types';

import { layoutNodes } from './layoutNodes';

afterEach(() => vi.useRealTimers());

it('preserves the Tidy Up layout, node data and order when using the shared helper', () => {
    vi.useFakeTimers();
    const nodes: FlowNode[] = ['trigger', 'globalNode', 'startCall', 'agentNode', 'agentNode', 'endCall', 'webhook', 'qa', 'customIntegration'].map((type, i) => ({
        id: String(i), type, position: { x: 0, y: 0 }, data: { name: `${type} ${i}` }, selected: i === 3,
    }));
    const edges: FlowEdge[] = [['2', '3'], ['2', '4'], ['3', '5'], ['4', '5']].map(([source, target]) => ({
        id: `${source}-${target}`, source, target, data: { label: 'Next', condition: 'Continue' },
    }));
    const before = structuredClone(nodes);
    const fitView = vi.fn();
    const instance = { current: { fitView } as unknown as ReactFlowInstance<FlowNode, FlowEdge> };

    const result = layoutNodes(nodes, edges, 'TB', instance);

    expect(result.map(node => node.position)).toEqual([
        { x: -825, y: 480 }, { x: -325, y: 480 },
        { x: 550, y: 60 }, { x: 175, y: 480 }, { x: 925, y: 480 }, { x: 550, y: 900 },
        { x: 1775, y: 210 }, { x: 1775, y: 480 }, { x: 1775, y: 750 },
    ]);
    expect(result.map(node => ({ ...node, position: { x: 0, y: 0 } }))).toEqual(before);
    expect(nodes).toEqual(before);
    vi.runAllTimers();
    expect(fitView).toHaveBeenCalledOnce();
});

it('does not rearrange a graph without conversation nodes', () => {
    const nodes: FlowNode[] = [{ id: 'global', type: 'globalNode', position: { x: 500, y: 200 }, data: { name: 'Rules' } }];
    expect(layoutNodes(nodes, [], 'TB', { current: null })).toBe(nodes);
});
