import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import { WorkflowBuilder } from './WorkflowBuilder';

const mocks = vi.hoisted(() => ({ get: vi.fn(), open: vi.fn(), send: vi.fn(), save: vi.fn(), push: vi.fn(), token: vi.fn(async () => 'token') }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: '1', organizationId: '7' }, loading: false, getAccessToken: mocks.token }) }));
vi.mock('@/client/sdk.gen', () => ({
    getBuilderSessionApiV1WorkflowBuilderSessionIdGet: mocks.get,
    openWorkflowBuilderSessionApiV1WorkflowBuilderWorkflowWorkflowIdSessionPost: mocks.open,
    saveBuilderSessionApiV1WorkflowBuilderSessionIdSavePost: mocks.save,
    sendBuilderTurnApiV1WorkflowBuilderSessionIdTurnPost: mocks.send,
}));
vi.mock('./WorkflowPreview', () => ({ WorkflowPreview: ({ onSave, disabled }: { onSave: () => void; disabled: boolean }) => <button disabled={disabled} onClick={onSave}>Save changes</button> }));

const session = {
    id: 'retained-session', checkpoint: 'old', messages: [{ id: 'message', role: 'user', content: 'Build a receptionist' }],
    pending: [], can_continue: false, proposal: { name: 'Agent', code: 'source', summary: 'Ready', workflow: { nodes: [], edges: [] } },
};

beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    mocks.open.mockResolvedValue({ data: session });
    mocks.send.mockImplementation(async () => ({ stream: (async function* () {
        yield { type: 'session', session: { ...session, workflow_id: 41, checkpoint: 'new', messages: [...session.messages, { id: 'reply', role: 'assistant', content: 'Updated greeting' }] } };
        yield { type: 'done' };
    })() }));
    mocks.save.mockResolvedValue({ data: { workflow_id: 41 } });
});

it('restores the retained session, continues it and saves into the same workflow', async () => {
    const onSaved = vi.fn(async () => undefined);
    render(<WorkflowBuilder workflowId={41} onSaved={onSaved} />);
    await screen.findByText('Build a receptionist');
    expect(mocks.open.mock.calls[0][0].path).toEqual({ workflow_id: 41 });
    expect(screen.queryByText('New conversation')).toBeNull();
    expect((screen.getByText('Save changes') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Change the greeting' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await screen.findByText('Updated greeting');
    expect(mocks.send.mock.calls[0][0]).toMatchObject({ path: { session_id: 'retained-session' }, body: { message: 'Change the greeting', checkpoint: 'old', workflow_id: 41 } });
    fireEvent.click(screen.getByText('Save changes'));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(mocks.save.mock.calls[0][0]).toMatchObject({ path: { session_id: 'retained-session' }, body: { checkpoint: 'new', workflow_id: 41 } });
    expect(mocks.push).not.toHaveBeenCalled();
});

it('blocks builder writes while the canvas has unsaved edits', async () => {
    mocks.open.mockResolvedValue({ data: { ...session, workflow_id: 41 } });
    render(<WorkflowBuilder workflowId={41} disabled />);
    await screen.findByText('Build a receptionist');
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByText('Save changes') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('status').textContent).toContain('Save your canvas changes');
});

it('shows save errors without navigating or refreshing the editor', async () => {
    mocks.open.mockResolvedValue({ data: { ...session, workflow_id: 41 } });
    mocks.save.mockResolvedValue({ error: { detail: 'The workflow changed since this proposal was built.' } });
    const onSaved = vi.fn(async () => undefined);
    render(<WorkflowBuilder workflowId={41} onSaved={onSaved} />);
    await screen.findByText('Build a receptionist');
    fireEvent.click(screen.getByText('Save changes'));
    expect((await screen.findByRole('alert')).textContent).toContain('The workflow changed');
    expect(onSaved).not.toHaveBeenCalled();
});


it('opens a new persistent conversation for a workflow that has never used the builder', async () => {
    mocks.open.mockResolvedValue({ data: { id: 'new-session', messages: [], pending: [], can_continue: false } });
    const { unmount } = render(<WorkflowBuilder workflowId={41} />);
    await screen.findByText('What would you like to change?');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Update this existing agent' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await screen.findByText('Updated greeting');
    expect(mocks.send.mock.calls[0][0]).toMatchObject({ path: { session_id: 'new-session' }, body: { workflow_id: 41, checkpoint: null } });
    unmount();
    mocks.open.mockResolvedValue({ data: { ...session, id: 'new-session' } });
    render(<WorkflowBuilder workflowId={41} />);
    await screen.findByText('Build a receptionist');
    expect(mocks.open).toHaveBeenCalledTimes(2);
});
