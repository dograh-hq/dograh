import { act, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkflowDetailPage from './page';

const mocks = vi.hoisted(() => ({
    query: 'version=3',
    auth: { user: { id: '1' }, loading: false, redirectToLogin: vi.fn() },
    workflow: vi.fn(),
    versions: vi.fn(),
}));

vi.mock('next/navigation', () => ({
    useParams: () => ({ workflowId: '12' }),
    useSearchParams: () => new URLSearchParams(mocks.query),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => mocks.auth }));
vi.mock('@/lib/logger', () => ({ default: { error: vi.fn() } }));
vi.mock('posthog-js', () => ({ default: { capture: vi.fn() } }));
vi.mock('@/client/sdk.gen', () => ({
    getWorkflowApiV1WorkflowFetchWorkflowIdGet: mocks.workflow,
    getWorkflowVersionsApiV1WorkflowWorkflowIdVersionsGet: mocks.versions,
}));
vi.mock('../WorkflowLayout', () => ({ default: ({ children }: { children: ReactNode }) => children }));
vi.mock('./RenderWorkflow', () => ({
    default: (props: Record<string, unknown>) => <pre data-testid="workflow">{JSON.stringify(props)}</pre>,
}));

const version = (number: number) => ({
    id: 100 + number,
    version_number: number,
    status: 'archived',
    workflow_json: { nodes: [{ id: `v${number}` }], edges: [] },
    workflow_configurations: { max_call_duration: number * 100 },
    template_context_variables: { company: `Version ${number}` },
});

const editorProps = () => JSON.parse(screen.getByTestId('workflow').textContent!);

beforeEach(() => {
    vi.clearAllMocks();
    mocks.query = 'version=3';
    mocks.auth.loading = false;
    mocks.workflow.mockResolvedValue({ data: {
        id: 12,
        name: 'Agent',
        workflow_definition: { nodes: [{ id: 'draft' }], edges: [] },
        workflow_configurations: { max_call_duration: 999 },
        template_context_variables: { company: 'Draft' },
        version_number: 20,
        version_status: 'draft',
    } });
    mocks.versions.mockResolvedValue({ data: [version(3)] });
});

describe('workflow version links', () => {
    it('loads the exact graph, configurations and variables without loading history', async () => {
        render(<WorkflowDetailPage />);
        await screen.findByTestId('workflow');
        expect(mocks.versions).toHaveBeenCalledWith({
            path: { workflow_id: 12 }, query: { version_number: 3, limit: 1 },
        });
        expect(editorProps().initialSelectedVersion.id).toBe(103);
        expect(editorProps().initialFlow.nodes).toEqual([{ id: 'v3' }]);
        expect(editorProps().initialWorkflowConfigurations).toEqual({ max_call_duration: 300 });
        expect(editorProps().initialTemplateContextVariables).toEqual({ company: 'Version 3' });
        expect(editorProps().initialVersionStatus).toBe('draft');
    });

    it('resolves latest to published even when an editable draft exists', async () => {
        mocks.query = 'version=latest';
        render(<WorkflowDetailPage />);
        await screen.findByTestId('workflow');
        expect(mocks.versions).toHaveBeenCalledWith({
            path: { workflow_id: 12 }, query: { status: 'published', limit: 1 },
        });
        expect(editorProps().initialFlow.nodes).toEqual([{ id: 'v3' }]);
    });

    it('waits for authentication before fetching either resource', async () => {
        mocks.auth.loading = true;
        const view = render(<WorkflowDetailPage />);
        expect(mocks.workflow).not.toHaveBeenCalled();
        expect(mocks.versions).not.toHaveBeenCalled();
        mocks.auth.loading = false;
        view.rerender(<WorkflowDetailPage />);
        await screen.findByTestId('workflow');
    });

    it.each(['', '0', '-1', 'abc', '1.5'])('rejects invalid version %j without opening the draft', async value => {
        mocks.query = `version=${value}`;
        render(<WorkflowDetailPage />);
        await screen.findByText(/Invalid workflow version/);
        expect(mocks.workflow).not.toHaveBeenCalled();
        expect(screen.queryByTestId('workflow')).toBeNull();
    });

    it('shows a missing version instead of silently opening the draft', async () => {
        mocks.versions.mockResolvedValue({ data: [] });
        render(<WorkflowDetailPage />);
        await screen.findByText('Workflow version not found');
        expect(screen.queryByTestId('workflow')).toBeNull();
    });

    it('follows query changes and restores the default editor when the parameter is removed', async () => {
        const view = render(<WorkflowDetailPage />);
        await screen.findByTestId('workflow');
        mocks.query = 'version=1';
        mocks.versions.mockResolvedValue({ data: [version(1)] });
        view.rerender(<WorkflowDetailPage />);
        await waitFor(() => expect(editorProps().initialSelectedVersion.id).toBe(101));
        mocks.query = '';
        view.rerender(<WorkflowDetailPage />);
        await waitFor(() => expect(editorProps().initialFlow.nodes).toEqual([{ id: 'draft' }]));
        expect(editorProps().initialSelectedVersion).toBeUndefined();
        expect(mocks.versions).toHaveBeenCalledTimes(2);
    });

    it('ignores a slow response for a version the user has left', async () => {
        let resolveOld!: (value: unknown) => void;
        mocks.versions.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
        const view = render(<WorkflowDetailPage />);
        await waitFor(() => expect(mocks.versions).toHaveBeenCalledTimes(1));
        mocks.query = 'version=1';
        mocks.versions.mockResolvedValue({ data: [version(1)] });
        view.rerender(<WorkflowDetailPage />);
        await screen.findByTestId('workflow');
        await act(async () => { resolveOld({ data: [version(3)] }); });
        expect(editorProps().initialSelectedVersion.id).toBe(101);
    });
});
