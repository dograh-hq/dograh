import { render, screen } from '@testing-library/react';
import type { ComponentProps, ReactNode } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';

import { WorkflowProvider } from '@/app/workflow/[workflowId]/contexts/WorkflowContext';
import type { DocumentResponseSchema, NodeSpec, ToolResponse } from '@/client/types.gen';

import { GenericNode } from './GenericNode';

const mocks = vi.hoisted(() => ({ saveNode: vi.fn(), saveWorkflow: vi.fn() }));
vi.mock('@xyflow/react', () => ({ NodeToolbar: () => null, Position: { Right: 'right' } }));
vi.mock('@/context/AppConfigContext', () => ({ useAppConfig: () => ({ config: {} }) }));
vi.mock('./common/NodeContent', () => ({ NodeContent: ({ children }: { children: ReactNode }) => children }));
vi.mock('./common/NodeEditDialog', () => ({ NodeEditDialog: () => null }));
vi.mock('./common/useNodeHandlers', () => ({
    useNodeHandlers: () => ({ open: false, setOpen: vi.fn(), handleSaveNodeData: mocks.saveNode, handleDeleteNode: vi.fn() }),
}));
vi.mock('@/components/flow/renderer', () => ({
    useNodeSpecs: () => ({ bySpecName: new Map([['agentNode', spec]]) }),
    NodeEditForm: () => null,
}));

const spec: NodeSpec = {
    name: 'agentNode', display_name: 'Agent', description: '', category: 'call_node', icon: 'Circle',
    properties: [
        { name: 'tool_uuids', display_name: 'Tools', description: '', type: 'tool_refs' },
        { name: 'document_uuids', display_name: 'Documents', description: '', type: 'document_refs' },
    ],
};
const props: ComponentProps<typeof GenericNode> = {
    id: 'agent', type: 'agentNode', data: { name: 'Agent', tool_uuids: ['new-tool'], document_uuids: ['new-document'] },
    selected: false, dragging: false, isConnectable: true, zIndex: 0,
    draggable: true, selectable: true, deletable: true, positionAbsoluteX: 0, positionAbsoluteY: 0,
};

beforeEach(() => vi.clearAllMocks());

it('preserves attached resources when the canvas catalog is stale, then displays refreshed names', () => {
    const canvas = (tools: ToolResponse[], documents: DocumentResponseSchema[]) => (
        <WorkflowProvider value={{ tools, documents, saveWorkflow: mocks.saveWorkflow }}>
            <GenericNode {...props} />
        </WorkflowProvider>
    );
    const { rerender } = render(canvas([], []));

    expect(mocks.saveNode).not.toHaveBeenCalled();
    expect(mocks.saveWorkflow).not.toHaveBeenCalled();
    expect(screen.getByText('Tool unavailable')).toBeTruthy();
    expect(screen.getByText('Document unavailable')).toBeTruthy();

    rerender(canvas(
        [{ tool_uuid: 'new-tool', name: 'Transfer to support', category: 'native' } as ToolResponse],
        [{ document_uuid: 'new-document', filename: 'Support guide.pdf' } as DocumentResponseSchema],
    ));
    expect(screen.getByText('Transfer to support')).toBeTruthy();
    expect(screen.getByText('Support guide.pdf')).toBeTruthy();
    expect(mocks.saveNode).not.toHaveBeenCalled();
    expect(mocks.saveWorkflow).not.toHaveBeenCalled();
});

it('keeps the enabled MCP functions visible', () => {
    render(
        <WorkflowProvider value={{
            tools: [{ tool_uuid: 'new-tool', name: 'Support server', category: 'mcp' } as ToolResponse],
            saveWorkflow: mocks.saveWorkflow,
        }}>
            <GenericNode {...props} data={{ ...props.data, document_uuids: [], mcp_tool_filters: { 'new-tool': ['lookup_ticket'] } }} />
        </WorkflowProvider>,
    );
    expect(screen.getByText('lookup_ticket')).toBeTruthy();
    expect(screen.queryByText('Support server')).toBeNull();
});
