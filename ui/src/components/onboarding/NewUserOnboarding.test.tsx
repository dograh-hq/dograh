import { render, screen, waitFor } from '@testing-library/react';
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
beforeEach(() => {
    vi.clearAllMocks();
    mocks.deploymentMode = 'oss';
    mocks.status = 'missing';
    mocks.completedAt = null;
    mocks.count.mockResolvedValue({ data: { total: 0, active: 0 } });
});

describe('first run', () => {
    it.each(['missing', 'invalid', 'connected', 'unknown'])('records OSS registration without setup when Dograh status is %s', async (status) => {
        mocks.status = status;
        render(<NewUserOnboarding />);
        await waitFor(() => expect(mocks.markCompleted).toHaveBeenCalledWith());
        expect(mocks.registration).toHaveBeenCalledOnce();
        expect(mocks.request).not.toHaveBeenCalled();
        expect(mocks.capture).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog')).toBeNull();
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
