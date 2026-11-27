import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import { useModelSetup } from './useModelSetup';

const mocks = vi.hoisted(() => ({
    status: 'missing', deploymentMode: 'oss', dismissed: new Set<string>(),
    organizationId: '1',
    refresh: vi.fn(), markAction: vi.fn(), push: vi.fn(), capture: vi.fn(), proceed: vi.fn(), cancelled: vi.fn(),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock('posthog-js', () => ({ default: { capture: mocks.capture } }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: '1', organizationId: mocks.organizationId }, loading: false }) }));
vi.mock('@/context/AppConfigContext', () => ({ useAppConfig: () => ({ config: { deploymentMode: mocks.deploymentMode } }) }));
vi.mock('@/context/DograhAccountContext', () => ({ useDograhAccount: () => ({ status: mocks.status, refresh: mocks.refresh }) }));
vi.mock('@/context/OnboardingContext', () => ({ useOnboarding: () => ({ loading: false, hasCompletedAction: (key: string) => mocks.dismissed.has(key), markActionCompleted: mocks.markAction }) }));
vi.mock('@/components/model-connections/DograhAccountConnect', () => ({
    DograhAccountConnectFlow: ({ onConnected, onPendingChange }: { onConnected: () => Promise<void>; onPendingChange: (pending: boolean) => void }) => <>
        <button onClick={() => onPendingChange(true)}>Start connecting</button>
        <button onClick={() => { onPendingChange(false); void onConnected(); }}>Approve on Dograh</button>
    </>,
}));

function Action({ source = 'call', enabled = true }: { source?: 'call' | 'builder'; enabled?: boolean }) {
    const { ensureSetup, setupModal, checking } = useModelSetup(source, enabled);
    return <>
        <button disabled={checking} onClick={async () => { if (await ensureSetup()) mocks.proceed(); else mocks.cancelled(); }}>Try action</button>
        {setupModal}
    </>;
}

beforeEach(() => {
    vi.clearAllMocks();
    mocks.status = 'missing';
    mocks.deploymentMode = 'oss';
    mocks.dismissed = new Set();
    mocks.organizationId = '1';
    mocks.markAction.mockImplementation((key: string) => { mocks.dismissed.add(key); });
});

it('prompts on the first call attempt and remembers an explicit skip across visits', async () => {
    const { unmount } = render(<Action />);
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByText('Try action'));
    expect(screen.getByRole('dialog', { name: 'Set up AI models' })).toBeTruthy();
    expect(mocks.proceed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Skip for now'));
    await waitFor(() => expect(mocks.proceed).toHaveBeenCalledOnce());
    expect(mocks.markAction).toHaveBeenCalledWith('model_setup_dismissed:1');
    unmount();
    render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    await waitFor(() => expect(mocks.proceed).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('dialog')).toBeNull();
});

it('still requires Dograh for the builder after the call prompt was skipped', async () => {
    mocks.dismissed.add('model_setup_dismissed:1');
    render(<Action source="builder" />);
    fireEvent.click(screen.getByText('Try action'));
    fireEvent.click(screen.getByText('Skip for now'));
    await waitFor(() => expect(mocks.cancelled).toHaveBeenCalledOnce());
    expect(mocks.proceed).not.toHaveBeenCalled();
});

it('cancels the attempt when the popup is closed without treating it as an explicit skip', async () => {
    render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(mocks.cancelled).toHaveBeenCalledOnce());
    expect(mocks.proceed).not.toHaveBeenCalled();
    expect(mocks.markAction).not.toHaveBeenCalled();
});

it('cannot close or navigate away through setup choices while waiting for approval', () => {
    render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    fireEvent.click(screen.getByText('Start connecting'));
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    for (const name of ['Skip for now', 'Add provider keys']) expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true);
    expect(mocks.proceed).not.toHaveBeenCalled();
});

it('routes to provider setup without starting a call', async () => {
    render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    fireEvent.click(screen.getByText('Add provider keys'));
    await waitFor(() => expect(mocks.cancelled).toHaveBeenCalledOnce());
    expect(mocks.push).toHaveBeenCalledWith('/provider-connections/new');
    expect(mocks.markAction).not.toHaveBeenCalled();
    expect(mocks.proceed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Try action'));
    expect(screen.getByRole('dialog', { name: 'Set up AI models' })).toBeTruthy();
});

it('offers setup in another organization after an explicit skip', async () => {
    const { rerender } = render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    fireEvent.click(screen.getByText('Skip for now'));
    await waitFor(() => expect(mocks.proceed).toHaveBeenCalledOnce());
    mocks.organizationId = '2';
    rerender(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    expect(screen.getByRole('dialog', { name: 'Set up AI models' })).toBeTruthy();
    expect(mocks.proceed).toHaveBeenCalledOnce();
});

it('resumes the pending action after connection without changing pages', async () => {
    mocks.status = 'invalid';
    render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    expect(screen.getByText('Reconnect Dograh models')).toBeTruthy();
    fireEvent.click(screen.getByText('Approve on Dograh'));
    expect(mocks.refresh).toHaveBeenCalledOnce();
    expect(mocks.proceed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Continue'));
    await waitFor(() => expect(mocks.proceed).toHaveBeenCalledOnce());
    expect(mocks.push).not.toHaveBeenCalled();
});

it('cancels a pending action on unmount', async () => {
    const { unmount } = render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    unmount();
    await waitFor(() => expect(mocks.cancelled).toHaveBeenCalledOnce());
    expect(mocks.proceed).not.toHaveBeenCalled();
});

it('cancels a pending action when its caller closes', async () => {
    const { rerender } = render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    rerender(<Action enabled={false} />);
    await waitFor(() => expect(mocks.cancelled).toHaveBeenCalledOnce());
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(mocks.proceed).not.toHaveBeenCalled();
});

it('cancels a pending action when the organization changes', async () => {
    const { rerender } = render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    mocks.organizationId = '2';
    rerender(<Action />);
    await waitFor(() => expect(mocks.cancelled).toHaveBeenCalledOnce());
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(mocks.proceed).not.toHaveBeenCalled();
});

it.each(['connected', 'unknown'])('does not show setup when the status is %s', async status => {
    mocks.status = status;
    render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    await waitFor(() => expect(mocks.proceed).toHaveBeenCalledOnce());
    expect(screen.queryByRole('dialog')).toBeNull();
});

it('leaves hosted calls unchanged', async () => {
    mocks.deploymentMode = 'saas';
    render(<Action />);
    fireEvent.click(screen.getByText('Try action'));
    await waitFor(() => expect(mocks.proceed).toHaveBeenCalledOnce());
    expect(screen.queryByRole('dialog')).toBeNull();
});
