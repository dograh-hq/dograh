import { beforeEach, describe, expect, it } from 'vitest';

import { FlowEdge, FlowNode } from '@/components/flow/types';

import { useWorkflowStore } from './workflowStore';

const createNode = (id: string, x = 0): FlowNode => ({
  id,
  type: 'agentNode',
  position: { x, y: 0 },
  data: { name: id },
});

const createEdge = (id: string, source: string, target: string): FlowEdge => ({
  id,
  source,
  target,
  data: { condition: 'always', label: id },
});

const nodeIds = () => useWorkflowStore.getState().nodes.map((node) => node.id);

describe('workflow history', () => {
  beforeEach(() => {
    useWorkflowStore.getState().clearStore();
    useWorkflowStore.getState().initializeWorkflow(1, 'Initial', [], []);
  });

  it('keeps every committed node state available to undo and redo', () => {
    const store = useWorkflowStore.getState();
    store.addNode(createNode('a'));
    store.addNode(createNode('b'));

    store.undo();
    expect(nodeIds()).toEqual(['a']);

    store.undo();
    expect(nodeIds()).toEqual([]);

    store.redo();
    store.redo();
    expect(nodeIds()).toEqual(['a', 'b']);
  });

  it('undoes a node and connected-edge deletion in one step', () => {
    const firstNode = createNode('a');
    const secondNode = createNode('b');
    const edge = createEdge('a-b', 'a', 'b');
    useWorkflowStore.getState().initializeWorkflow(
      1,
      'Initial',
      [firstNode, secondNode],
      [edge]
    );

    // React Flow emits connected-edge removals before node removals, then onDelete.
    useWorkflowStore.getState().setEdges(
      [],
      [{ id: edge.id, type: 'remove' }]
    );
    useWorkflowStore.getState().setNodes(
      [secondNode],
      [{ id: firstNode.id, type: 'remove' }]
    );
    useWorkflowStore.getState().commitDeletion();
    expect(nodeIds()).toEqual(['b']);
    expect(useWorkflowStore.getState().edges).toEqual([]);
    expect(useWorkflowStore.getState().history).toHaveLength(2);

    useWorkflowStore.getState().undo();
    expect(nodeIds()).toEqual(['a', 'b']);
    expect(useWorkflowStore.getState().edges).toEqual([edge]);

    useWorkflowStore.getState().redo();
    expect(nodeIds()).toEqual(['b']);
    expect(useWorkflowStore.getState().edges).toEqual([]);
  });

  it('coalesces active dragging into one committed final position', () => {
    const initialNode = createNode('a');
    useWorkflowStore.getState().initializeWorkflow(1, 'Initial', [initialNode], []);

    for (const x of [10, 20]) {
      useWorkflowStore.getState().setNodes(
        [createNode('a', x)],
        [{ id: 'a', type: 'position', position: { x, y: 0 }, dragging: true }]
      );
    }
    useWorkflowStore.getState().setNodes(
      [createNode('a', 30)],
      [{ id: 'a', type: 'position', position: { x: 30, y: 0 }, dragging: false }]
    );

    expect(useWorkflowStore.getState().history).toHaveLength(2);
    useWorkflowStore.getState().undo();
    expect(useWorkflowStore.getState().nodes[0].position.x).toBe(0);
    useWorkflowStore.getState().redo();
    expect(useWorkflowStore.getState().nodes[0].position.x).toBe(30);
  });

  it('truncates redo history after a new edit', () => {
    useWorkflowStore.getState().addNode(createNode('a'));
    useWorkflowStore.getState().addNode(createNode('b'));
    useWorkflowStore.getState().undo();
    useWorkflowStore.getState().addNode(createNode('c'));

    expect(nodeIds()).toEqual(['a', 'c']);
    expect(useWorkflowStore.getState().canRedo()).toBe(false);

    useWorkflowStore.getState().undo();
    expect(nodeIds()).toEqual(['a']);
    useWorkflowStore.getState().redo();
    expect(nodeIds()).toEqual(['a', 'c']);
  });

  it('keeps the current state at the end of the bounded history', () => {
    for (let index = 1; index <= 55; index += 1) {
      useWorkflowStore.getState().setWorkflowName(`Edit ${index}`);
    }

    expect(useWorkflowStore.getState().history).toHaveLength(50);
    expect(useWorkflowStore.getState().historyIndex).toBe(49);

    let undoCount = 0;
    while (useWorkflowStore.getState().canUndo()) {
      useWorkflowStore.getState().undo();
      undoCount += 1;
    }
    expect(undoCount).toBe(49);
    expect(useWorkflowStore.getState().workflowName).toBe('Edit 6');

    while (useWorkflowStore.getState().canRedo()) {
      useWorkflowStore.getState().redo();
    }
    expect(useWorkflowStore.getState().workflowName).toBe('Edit 55');
  });

  it('does not track selection-only changes', () => {
    const initialNode = createNode('a');
    useWorkflowStore.getState().initializeWorkflow(1, 'Initial', [initialNode], []);

    useWorkflowStore.getState().setNodes(
      [{ ...initialNode, selected: true }],
      [{ id: 'a', type: 'select', selected: true }]
    );

    expect(useWorkflowStore.getState().nodes[0].selected).toBe(true);
    expect(useWorkflowStore.getState().history).toHaveLength(1);
    expect(useWorkflowStore.getState().isDirty).toBe(false);
  });
});
