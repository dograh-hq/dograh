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

const session = {
    id: 'retained-session', checkpoint: 'old', messages: [{ id: 'message', role: 'user', content: 'Build a receptionist' }],
    pending: [], can_continue: false, proposal: { name: 'Agent', code: 'source', summary: 'Ready', workflow: { nodes: [], edges: [] } },
};

beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    window.history.replaceState({}, '', '/workflow/create?session=retained-session');
    mocks.get.mockResolvedValue({ data: session });
    mocks.open.mockResolvedValue({ data: session });
    mocks.send.mockImplementation(async () => ({ stream: (async function* () {
        yield { type: 'session', session: { ...session, workflow_id: 41, checkpoint: 'new', messages: [...session.messages, { id: 'reply', role: 'assistant', content: 'Updated greeting' }] } };
        yield { type: 'done' };
    })() }));
    mocks.save.mockResolvedValue({ data: { workflow_id: 41 } });
});

it('offers testing instead of a preview and opens the saved workflow in the builder', async () => {
    mocks.get.mockResolvedValue({ data: { ...session, messages: [...session.messages, {
        id: 'ready', role: 'assistant', content: 'Your agent is ready.\n\nReview the preview and save a draft when ready.',
    }] } });
    let finishSave!: (value: { data: { workflow_id: number } }) => void;
    mocks.save.mockReturnValue(new Promise(resolve => { finishSave = resolve; }));
    render(<WorkflowBuilder />);

    const testAgent = await screen.findByRole('button', { name: 'Test agent' });
    expect(screen.getByText('Your agent is ready.')).toBeTruthy();
    expect(screen.queryByText('Save changes')).toBeNull();
    expect(screen.queryByText('Click a node to read its prompt.')).toBeNull();
    expect(mocks.get.mock.calls[0][0].path).toEqual({ session_id: 'retained-session' });
    fireEvent.click(testAgent);

    expect((await screen.findByRole('button', { name: 'Preparing agent…' }) as HTMLButtonElement).disabled).toBe(true);
    expect(mocks.push).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
    expect(mocks.save.mock.calls[0][0]).toMatchObject({ path: { session_id: 'retained-session' }, body: { checkpoint: 'old' } });
    finishSave({ data: { workflow_id: 41 } });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/workflow/41?view=builder'));
});

it('keeps the conversation available to retry when preparing the agent fails', async () => {
    mocks.save.mockResolvedValue({ error: { detail: 'The latest proposal could not be saved.' } });
    render(<WorkflowBuilder />);
    fireEvent.click(await screen.findByRole('button', { name: 'Test agent' }));
    expect((await screen.findByRole('alert')).textContent).toContain('The latest proposal could not be saved.');
    expect(mocks.push).not.toHaveBeenCalled();
    expect((screen.getByRole('button', { name: 'Test agent' }) as HTMLButtonElement).disabled).toBe(false);
});

it('waits for a proposal before offering to test the agent', async () => {
    mocks.get.mockResolvedValue({ data: { ...session, proposal: null } });
    render(<WorkflowBuilder />);
    await screen.findByText('Build a receptionist');
    expect(screen.queryByRole('button', { name: 'Test agent' })).toBeNull();
});

it('blocks testing an unfinished build', async () => {
    mocks.get.mockResolvedValue({ data: { ...session, can_continue: true } });
    render(<WorkflowBuilder />);
    const testAgent = await screen.findByRole('button', { name: 'Test agent' });
    expect((testAgent as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(testAgent);
    expect(mocks.save).not.toHaveBeenCalled();
});

it('applies completed changes automatically to the same workflow and refreshes the editor', async () => {
    const onSaved = vi.fn(async () => undefined);
    const onBusyChange = vi.fn();
    render(<WorkflowBuilder workflowId={41} onSaved={onSaved} onBusyChange={onBusyChange} />);
    await screen.findByText('Build a receptionist');
    expect(mocks.open.mock.calls[0][0].path).toEqual({ workflow_id: 41 });
    expect(screen.queryByText('New conversation')).toBeNull();
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull();
    expect(screen.queryByRole('tab', { name: /workflow|code/i })).toBeNull();
    expect(mocks.save).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Change the greeting' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await screen.findByText('Updated greeting');
    expect(mocks.send.mock.calls[0][0]).toMatchObject({ path: { session_id: 'retained-session' }, body: { message: 'Change the greeting', checkpoint: 'old', workflow_id: 41 } });
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
    expect(onBusyChange).toHaveBeenCalledWith(true);
    expect(mocks.save.mock.calls[0][0]).toMatchObject({ path: { session_id: 'retained-session' }, body: { checkpoint: 'new', workflow_id: 41 } });
    expect(mocks.push).not.toHaveBeenCalled();
});

it('blocks builder writes while the canvas has unsaved edits', async () => {
    mocks.open.mockResolvedValue({ data: { ...session, workflow_id: 41 } });
    render(<WorkflowBuilder workflowId={41} disabled />);
    await screen.findByText('Build a receptionist');
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toContain('Save your canvas changes');
});

it('shows automatic update errors without navigating or refreshing the editor', async () => {
    mocks.open.mockResolvedValue({ data: { ...session, workflow_id: 41 } });
    mocks.save.mockResolvedValue({ error: { detail: 'The workflow changed since this proposal was built.' } });
    const onSaved = vi.fn(async () => undefined);
    render(<WorkflowBuilder workflowId={41} onSaved={onSaved} />);
    await screen.findByText('Build a receptionist');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Change the greeting' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    expect((await screen.findByRole('alert')).textContent).toContain('The workflow changed');
    expect(onSaved).not.toHaveBeenCalled();
});

it('keeps testing blocked until the completed changes have been applied', async () => {
    let finishSave!: (value: { data: { workflow_id: number } }) => void;
    mocks.save.mockReturnValue(new Promise(resolve => { finishSave = resolve; }));
    const onBusyChange = vi.fn();
    const onSaved = vi.fn(async () => undefined);
    render(<WorkflowBuilder workflowId={41} onSaved={onSaved} onBusyChange={onBusyChange} />);
    await screen.findByText('Build a receptionist');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Change the greeting' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await screen.findByText('Updating your agent…');
    expect(onBusyChange).toHaveBeenLastCalledWith(true);
    expect(onSaved).not.toHaveBeenCalled();
    finishSave({ data: { workflow_id: 41 } });
    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
    expect(onSaved).toHaveBeenCalledOnce();
});

it.each(['unfinished', 'interrupted'])('does not apply an %s build', async state => {
    mocks.send.mockImplementation(async () => ({ stream: (async function* () {
        yield { type: 'session', session: { ...session, workflow_id: 41, can_continue: state === 'unfinished' } };
        if (state !== 'interrupted') yield { type: 'done' };
    })() }));
    const onBusyChange = vi.fn();
    render(<WorkflowBuilder workflowId={41} onBusyChange={onBusyChange} />);
    await screen.findByText('Build a receptionist');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Change the greeting' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
    expect(mocks.save).not.toHaveBeenCalled();
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
