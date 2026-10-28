import { ReactFlowInstance } from '@xyflow/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { FlowEdge, FlowNode } from '@/components/flow/types';

import { layoutNodes } from './layoutNodes';

const { layoutWorkflow } = vi.hoisted(() => ({ layoutWorkflow: vi.fn() }));
vi.mock('@/client', () => ({ layoutWorkflowApiV1WorkflowLayoutPost: layoutWorkflow }));

afterEach(() => {
    vi.useRealTimers();
    layoutWorkflow.mockReset();
});

const node = (id: string, type: string): FlowNode => ({ id, type, position: { x: 0, y: 0 }, data: { name: `${type} ${id}` } });
const edge = (source: string, target: string): FlowEdge => ({ id: `${source}-${target}`, source, target, data: { label: 'Next', condition: 'Continue' } });

it('sends only ids, types and edge endpoints, then applies the returned positions', async () => {
    vi.useFakeTimers();
    const nodes = [node('1', 'startCall'), { ...node('2', 'agentNode'), selected: true }, node('3', 'endCall')];
    const edges = [edge('1', '2'), edge('2', '3')];
    const before = structuredClone(nodes);
    layoutWorkflow.mockResolvedValue({ data: { positions: [{ id: '1', x: 175, y: 60 }, { id: '2', x: 775, y: 480 }, { id: '3', x: 175, y: 900 }] } });
    const fitView = vi.fn();
    const instance = { current: { fitView } as unknown as ReactFlowInstance<FlowNode, FlowEdge> };

    const result = await layoutNodes(nodes, edges, 'TB', instance);

    expect(layoutWorkflow).toHaveBeenCalledWith({
        body: {
            nodes: [{ id: '1', type: 'startCall' }, { id: '2', type: 'agentNode' }, { id: '3', type: 'endCall' }],
            edges: [{ source: '1', target: '2' }, { source: '2', target: '3' }],
            rankdir: 'TB',
        },
    });
    expect(result.map(n => n.position)).toEqual([{ x: 175, y: 60 }, { x: 775, y: 480 }, { x: 175, y: 900 }]);
    expect(result.map(n => ({ ...n, position: { x: 0, y: 0 } }))).toEqual(before);
    expect(nodes).toEqual(before);
    vi.runAllTimers();
    expect(fitView).toHaveBeenCalledOnce();
});

it('leaves the canvas alone when the API has nothing to lay out', async () => {
    layoutWorkflow.mockResolvedValue({ data: { positions: [] } });
    const nodes = [{ ...node('global', 'globalNode'), position: { x: 500, y: 200 } }];
    await expect(layoutNodes(nodes, [], 'TB', { current: null })).resolves.toBe(nodes);
});

it('surfaces the API error message', async () => {
    layoutWorkflow.mockResolvedValue({ error: { detail: 'Layout is unavailable right now. Try again.' } });
    await expect(layoutNodes([node('1', 'startCall')], [], 'TB', { current: null }))
        .rejects.toThrow('Layout is unavailable right now. Try again.');
});
