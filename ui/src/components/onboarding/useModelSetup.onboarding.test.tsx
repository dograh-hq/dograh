import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import { OnboardingProvider } from '@/context/OnboardingContext';

import { useModelSetup } from './useModelSetup';

const mocks = vi.hoisted(() => ({ read: vi.fn(), proceed: vi.fn(), status: 'missing' }));
vi.mock('@/client/sdk.gen', () => ({ getUserOnboardingStateApiV1UserOnboardingStateGet: mocks.read }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: '1' }, isAuthenticated: true, loading: false }) }));
vi.mock('@/context/AppConfigContext', () => ({ useAppConfig: () => ({ config: { deploymentMode: 'oss' } }) }));
vi.mock('@/context/DograhAccountContext', () => ({ useDograhAccount: () => ({ status: mocks.status, refresh: vi.fn() }) }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('posthog-js', () => ({ default: { capture: vi.fn() } }));

function Call() {
    const { checking, ensureSetup, setupModal } = useModelSetup('call');
    return <>
        <button disabled={checking} onClick={async () => { if (await ensureSetup()) mocks.proceed(); }}>Start call</button>
        {setupModal}
    </>;
}

beforeEach(() => {
    vi.clearAllMocks();
    mocks.status = 'missing';
});

it.each(['missing', 'invalid'])('allows calls when onboarding fails and the Dograh status is %s', async status => {
    mocks.status = status;
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
    mocks.read.mockResolvedValue({ error: { detail: 'Service unavailable' } });
    render(<OnboardingProvider><Call /></OnboardingProvider>);
    const button = screen.getByRole('button', { name: 'Start call' }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(mocks.proceed).toHaveBeenCalledOnce());
    expect(screen.queryByRole('dialog')).toBeNull();
    errorLog.mockRestore();
});

it('recovers from a network failure while reading onboarding', async () => {
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
    mocks.read.mockRejectedValue(new Error('Network error'));
    render(<OnboardingProvider><Call /></OnboardingProvider>);
    const button = screen.getByRole('button', { name: 'Start call' }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(mocks.proceed).toHaveBeenCalledOnce());
    errorLog.mockRestore();
});

it('still waits for an in-flight preference read before offering setup', async () => {
    let resolve!: (value: unknown) => void;
    mocks.read.mockReturnValue(new Promise(done => { resolve = done; }));
    render(<OnboardingProvider><Call /></OnboardingProvider>);
    const button = screen.getByRole('button', { name: 'Start call' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(mocks.proceed).not.toHaveBeenCalled();
    await act(async () => { resolve({ data: { completed_actions: [] } }); });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(screen.getByRole('dialog', { name: 'Set up AI models' })).toBeTruthy();
    expect(mocks.proceed).not.toHaveBeenCalled();
});
