import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import { DograhAccountProvider, useDograhAccount } from './DograhAccountContext';

const mocks = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock('@/client/sdk.gen', () => ({ getDograhAccountStatus: mocks.status }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: '1', organizationId: '7' }, loading: false }) }));

let refresh: () => Promise<void> = async () => {};

function Status({ enabled = true }: { enabled?: boolean }) {
    const dograh = useDograhAccount(enabled);
    refresh = dograh.refresh;
    return <p>{dograh.status}</p>;
}

beforeEach(() => {
    vi.clearAllMocks();
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
