import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import { DograhAccountProvider, useDograhAccount } from './DograhAccountContext';

const mocks = vi.hoisted(() => ({
    status: vi.fn(),
    auth: { user: { id: '1', organizationId: '7' }, loading: false },
}));
vi.mock('@/client/sdk.gen', () => ({ getDograhAccountStatus: mocks.status }));
vi.mock('@/lib/auth', () => ({ useAuth: () => mocks.auth }));

function deferred() {
    let resolve!: (value: unknown) => void;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

let refresh: () => Promise<void> = async () => {};

function Status({ enabled = true }: { enabled?: boolean }) {
    const dograh = useDograhAccount(enabled);
    refresh = dograh.refresh;
    return <p>{dograh.status}</p>;
}

beforeEach(() => {
    vi.clearAllMocks();
    mocks.auth = { user: { id: '1', organizationId: '7' }, loading: false };
    mocks.status.mockResolvedValue({ data: { status: 'missing' } });
});

it('reads the status only once a caller asks for it', async () => {
    const { rerender } = render(<DograhAccountProvider><Status enabled={false} /></DograhAccountProvider>);
    expect(screen.getByText('loading')).toBeDefined();
    expect(mocks.status).not.toHaveBeenCalled();
    rerender(<DograhAccountProvider><Status /></DograhAccountProvider>);
    expect(await screen.findByText('missing')).toBeDefined();
    expect(mocks.status).toHaveBeenCalledOnce();
});

it('shares one read between callers and reads again on refresh', async () => {
    render(<DograhAccountProvider><Status /><Status /></DograhAccountProvider>);
    await waitFor(() => expect(screen.getAllByText('missing')).toHaveLength(2));
    expect(mocks.status).toHaveBeenCalledOnce();
    mocks.status.mockResolvedValue({ data: { status: 'connected' } });
    await act(async () => { await refresh(); });
    expect(screen.getAllByText('connected')).toHaveLength(2);
});

it('reports an unreadable status as unknown', async () => {
    mocks.status.mockResolvedValue({ error: { detail: 'Server error' } });
    render(<DograhAccountProvider><Status /></DograhAccountProvider>);
    expect(await screen.findByText('unknown')).toBeDefined();
});

it('reports a failed read as unknown', async () => {
    mocks.status.mockRejectedValue(new Error('offline'));
    render(<DograhAccountProvider><Status /></DograhAccountProvider>);
    expect(await screen.findByText('unknown')).toBeDefined();
});

it('keeps the last status on screen while refreshing', async () => {
    render(<DograhAccountProvider><Status /></DograhAccountProvider>);
    expect(await screen.findByText('missing')).toBeDefined();
    const next = deferred();
    mocks.status.mockReturnValue(next.promise);
    let done: Promise<void> = Promise.resolve();
    act(() => { done = refresh(); });
    expect(screen.getByText('missing')).toBeDefined();
    await act(async () => { next.resolve({ data: { status: 'connected' } }); await done; });
    expect(screen.getByText('connected')).toBeDefined();
});

it('reads again for a caller that mounts later, hiding a stale blocking status', async () => {
    const { rerender } = render(<DograhAccountProvider><Status /></DograhAccountProvider>);
    expect(await screen.findByText('missing')).toBeDefined();
    const next = deferred();
    mocks.status.mockReturnValue(next.promise);
    rerender(<DograhAccountProvider><Status /><Status /></DograhAccountProvider>);
    expect(screen.getAllByText('loading')).toHaveLength(2);
    await act(async () => { next.resolve({ data: { status: 'connected' } }); });
    expect(screen.getAllByText('connected')).toHaveLength(2);
    expect(mocks.status).toHaveBeenCalledTimes(2);
});

it('keeps a connected status visible while a new caller reads it again', async () => {
    mocks.status.mockResolvedValue({ data: { status: 'connected' } });
    const { rerender } = render(<DograhAccountProvider><Status /></DograhAccountProvider>);
    expect(await screen.findByText('connected')).toBeDefined();
    mocks.status.mockReturnValue(new Promise(() => {}));
    rerender(<DograhAccountProvider><Status /><Status /></DograhAccountProvider>);
    expect(screen.getAllByText('connected')).toHaveLength(2);
});

it('ignores a read for an organization that is no longer selected', async () => {
    const first = deferred();
    mocks.status.mockReturnValueOnce(first.promise).mockResolvedValueOnce({ data: { status: 'connected' } });
    const { rerender } = render(<DograhAccountProvider><Status /></DograhAccountProvider>);
    mocks.auth = { user: { id: '1', organizationId: '8' }, loading: false };
    rerender(<DograhAccountProvider><Status /></DograhAccountProvider>);
    expect(await screen.findByText('connected')).toBeDefined();
    await act(async () => { first.resolve({ data: { status: 'missing' } }); });
    expect(screen.getByText('connected')).toBeDefined();
});
