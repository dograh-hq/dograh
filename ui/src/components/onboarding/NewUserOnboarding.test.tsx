import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { NewUserOnboarding } from './NewUserOnboarding';

const mocks = vi.hoisted(() => ({
    count: vi.fn(),
    push: vi.fn(),
    capture: vi.fn(),
    registration: vi.fn(),
    markCompleted: vi.fn(),
    request: vi.fn(),
    refresh: vi.fn(),
    deploymentMode: 'oss',
    status: 'missing',
    completedAt: null as string | null,
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock('posthog-js', () => ({ default: { capture: mocks.capture } }));
vi.mock('@/client/sdk.gen', () => ({ getWorkflowCountApiV1WorkflowCountGet: mocks.count }));
vi.mock('@/lib/metaPixel', () => ({ trackMetaCompleteRegistration: mocks.registration }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: '1' }, loading: false }) }));
vi.mock('@/context/AppConfigContext', () => ({ useAppConfig: () => ({ config: { deploymentMode: mocks.deploymentMode } }) }));
vi.mock('@/context/OnboardingContext', () => ({
    useOnboarding: () => ({ loading: false, onboardingCompletedAt: mocks.completedAt, onboardingSkipped: false, markOnboardingCompleted: mocks.markCompleted }),
}));
vi.mock('@/context/DograhAccountContext', () => ({
    useDograhAccountContext: () => ({ status: mocks.status, request: mocks.request, refresh: mocks.refresh }),
}));
// The device flow has its own tests; here it only needs to report a connection.
vi.mock('@/components/model-connections/DograhAccountConnect', () => ({
    DograhAccountConnectFlow: ({ onConnected }: { onConnected: () => Promise<void> }) =>
        <button type="button" onClick={() => void onConnected()}>Approve on Dograh</button>,
}));

beforeEach(() => {
    vi.clearAllMocks();
    mocks.deploymentMode = 'oss';
    mocks.status = 'missing';
    mocks.completedAt = null;
    mocks.count.mockResolvedValue({ data: { total: 0, active: 0 } });
});

describe('first run', () => {
    it('asks a new self-hosted user to set up AI models when no Dograh key works', async () => {
        render(<NewUserOnboarding />);
        expect(await screen.findByRole('dialog', { name: 'Set up AI models' })).toBeDefined();
        expect(mocks.request).toHaveBeenCalledOnce();
        expect(mocks.registration).toHaveBeenCalledOnce();
        expect(mocks.capture).toHaveBeenCalledWith('model_setup_shown', { dograh_status: 'missing' });
        expect(mocks.markCompleted).not.toHaveBeenCalled();
        expect(screen.queryByText(/How did you hear about us/)).toBeNull();
    });

    it('offers to reconnect when the existing key is rejected', async () => {
        mocks.status = 'invalid';
        render(<NewUserOnboarding />);
        expect(await screen.findByText('Reconnect your Dograh account')).toBeDefined();
    });

    it.each(['connected', 'unknown'])('shows nothing to a self-hosted user whose status is %s', async (status) => {
        mocks.status = status;
        render(<NewUserOnboarding />);
        await waitFor(() => expect(mocks.request).toHaveBeenCalledOnce());
        expect(screen.queryByRole('dialog')).toBeNull();
        // Connected is done; an unreadable status asks again next session.
        if (status === 'connected') expect(mocks.markCompleted).toHaveBeenCalledWith();
        else expect(mocks.markCompleted).not.toHaveBeenCalled();
    });

    it('shows hosted users nothing and records their registration once', async () => {
        mocks.deploymentMode = 'saas';
        render(<NewUserOnboarding />);
        await waitFor(() => expect(mocks.markCompleted).toHaveBeenCalledWith());
        expect(mocks.registration).toHaveBeenCalledOnce();
        expect(mocks.request).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('leaves users whose organization already has agents alone', async () => {
        mocks.count.mockResolvedValue({ data: { total: 3, active: 3 } });
        render(<NewUserOnboarding />);
        await waitFor(() => expect(mocks.count).toHaveBeenCalledOnce());
        expect(mocks.registration).not.toHaveBeenCalled();
        expect(mocks.markCompleted).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('never checks again once onboarding is done', () => {
        mocks.completedAt = '2026-11-01T00:00:00Z';
        render(<NewUserOnboarding />);
        expect(mocks.count).not.toHaveBeenCalled();
    });
});

describe('Set up AI models', () => {
    it('can be skipped, and is not shown again', async () => {
        render(<NewUserOnboarding />);
        fireEvent.click(await screen.findByRole('button', { name: 'Skip for now' }));
        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
        expect(mocks.markCompleted).toHaveBeenCalledWith({ skipped: true });
        expect(mocks.capture).toHaveBeenCalledWith('model_setup_completed', { choice: 'skipped' });
    });

    it('sends people who bring their own keys to add a provider', async () => {
        render(<NewUserOnboarding />);
        fireEvent.click(await screen.findByRole('button', { name: 'Add provider keys' }));
        expect(mocks.push).toHaveBeenCalledWith('/provider-connections/new');
        expect(mocks.markCompleted).toHaveBeenCalledWith({ skipped: false });
        expect(mocks.capture).toHaveBeenCalledWith('model_setup_completed', { choice: 'own_keys' });
    });

    it('confirms a connected account, then opens the agent builder', async () => {
        render(<NewUserOnboarding />);
        fireEvent.click(await screen.findByRole('button', { name: 'Approve on Dograh' }));
        expect(await screen.findByText('Dograh account connected')).toBeDefined();
        expect(mocks.refresh).toHaveBeenCalledOnce();
        expect(mocks.markCompleted).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: /Build your first agent/ }));
        expect(mocks.push).toHaveBeenCalledWith('/workflow/create');
        expect(mocks.markCompleted).toHaveBeenCalledWith({ skipped: false });
        expect(mocks.capture).toHaveBeenCalledWith('model_setup_completed', { choice: 'dograh' });
    });
});
