import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';

import type { WorkflowVersionResponse } from '@/client/types.gen';

import RenderWorkflow from './RenderWorkflow';

const mocks = vi.hoisted(() => ({
    query: 'version=3&source=campaign',
    push: vi.fn(),
    versions: vi.fn(),
    save: vi.fn(),
    state: {
        rfInstance: { current: null },
        nodes: [], edges: [], workflowValidationErrors: [],
        workflowName: 'Agent', isDirty: false, templateContextVariables: {},
        setNodes: vi.fn(), setEdges: vi.fn(), setIsDirty: vi.fn(), setIsAddNodePanelOpen: vi.fn(),
    },
}));
vi.mock('@xyflow/react/dist/style.css', () => ({}));
vi.mock('next/navigation', () => ({
    useRouter: () => ({ push: mocks.push }),
    useSearchParams: () => new URLSearchParams(mocks.query),
}));
vi.mock('@/client', () => ({
    getWorkflowVersionsApiV1WorkflowWorkflowIdVersionsGet: mocks.versions,
    createWorkflowDraftApiV1WorkflowWorkflowIdCreateDraftPost: vi.fn(),
    listDocumentsApiV1KnowledgeBaseDocumentsGet: async () => ({ data: { documents: [] } }),
    listToolsApiV1ToolsGet: async () => ({ data: [] }),
    listRecordingsApiV1WorkflowRecordingsGet: async () => ({ data: { recordings: [] } }),
}));
vi.mock('./hooks/useWorkflowState', () => ({ useWorkflowState: () => ({ ...mocks.state, saveWorkflow: mocks.save }) }));
vi.mock('@/components/flow/renderer', () => ({ useNodeSpecs: () => ({ specs: [] }) }));
vi.mock('@/context/OnboardingContext', () => ({ useOnboarding: () => ({ hasCompletedAction: () => true }) }));
vi.mock('@/components/lead-forms/HireExpertNudge', () => ({ HireExpertNudge: () => null }));
vi.mock('@/components/flow/AddNodePanel', () => ({ default: () => null }));
vi.mock('@/components/flow/edges/CustomEdge', () => ({ default: () => null }));
vi.mock('@/components/flow/nodes/GenericNode', () => ({ GenericNode: () => null }));
vi.mock('./components/PhoneCallDialog', () => ({ PhoneCallDialog: () => null }));
vi.mock('./components/WorkflowVersionDiffDialog', () => ({ WorkflowVersionDiffDialog: () => null }));
vi.mock('./components/WorkflowTesterPanel', () => ({
    WorkflowTesterPanel: ({ disabled }: { disabled: boolean }) => <div data-testid="tester" data-disabled={disabled} />,
}));
vi.mock('@xyflow/react', () => ({
    Background: () => null,
    BackgroundVariant: { Dots: 'dots' },
    Panel: ({ children }: { children: ReactNode }) => children,
    ReactFlow: ({ nodesDraggable, children }: { nodesDraggable: boolean; children: ReactNode }) => <div data-testid="canvas" data-editable={nodesDraggable}>{children}</div>,
}));
vi.mock('./components/WorkflowEditorHeader', () => ({
    WorkflowEditorHeader: ({ activeVersionLabel, onHistoryClick, onBackToDraft, saveWorkflow }: {
        activeVersionLabel: string;
        onHistoryClick: () => void;
        onBackToDraft: () => void;
        saveWorkflow: () => void;
    }) => <div>
        <span data-testid="version-label">{activeVersionLabel}</span>
        <button onClick={onHistoryClick}>History</button>
        <button onClick={onBackToDraft}>Back to draft</button>
        <button onClick={saveWorkflow}>Save</button>
    </div>,
}));

const version = (number: number, status: string): WorkflowVersionResponse => ({
    id: 100 + number, version_number: number, status,
    workflow_json: { nodes: [], edges: [] }, created_at: '2026-01-01T00:00:00Z',
});

beforeEach(() => {
    vi.clearAllMocks();
    mocks.query = 'version=3&source=campaign';
    mocks.versions.mockResolvedValue({ data: [version(21, 'draft'), version(20, 'published')] });
});

it.each(['archived', 'published'])('opens a linked %s version read-only before history loads', async status => {
    render(<RenderWorkflow workflowId={12} initialWorkflowName="Agent" user={{ id: '1' }} initialSelectedVersion={version(3, status)} />);
    expect(screen.getByTestId('canvas').getAttribute('data-editable')).toBe('false');
    expect(screen.getByTestId('tester').getAttribute('data-disabled')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.versions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    await screen.findByRole('button', { name: /^v20/ });
    expect(screen.getByTestId('version-label').textContent).toContain('v3');
    expect(screen.getByTestId('canvas').getAttribute('data-editable')).toBe('false');
});

it('updates shareable URLs from history and clears only the version when returning to the draft', async () => {
    render(<RenderWorkflow workflowId={12} initialWorkflowName="Agent" user={{ id: '1' }} initialSelectedVersion={version(3, 'archived')} />);
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    fireEvent.click(await screen.findByRole('button', { name: /^v20/ }));
    expect(mocks.push).toHaveBeenCalledWith('/workflow/12?version=20&source=campaign', { scroll: false });
    fireEvent.click(screen.getByRole('button', { name: 'Back to draft' }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/workflow/12?source=campaign', { scroll: false }));
});

it.each(['current', 'historical'])('selects the editable published version from the %s view when no draft exists', async view => {
    mocks.query = view === 'current' ? 'source=campaign' : 'version=3&source=campaign';
    mocks.versions.mockResolvedValue({ data: [version(20, 'published'), version(3, 'archived')] });
    render(<RenderWorkflow
        workflowId={12}
        initialWorkflowName="Agent"
        initialVersionNumber={20}
        initialVersionStatus="published"
        initialSelectedVersion={view === 'historical' ? version(3, 'archived') : undefined}
        user={{ id: '1' }}
    />);

    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    fireEvent.click(await screen.findByRole('button', { name: /^v20/ }));

    expect(mocks.push).toHaveBeenCalledWith('/workflow/12?source=campaign', { scroll: false });
});
