import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import GetServiceKeyPage from './page';

const state = vi.hoisted(() => ({
    user: null as null | { primaryEmail: string; primaryEmailVerified: boolean; selectedTeam: { displayName: string } | null },
    provider: 'stack',
    loading: false,
    sendEmail: vi.fn(),
    createKey: vi.fn(),
    getAccessToken: vi.fn(),
}));
vi.mock('@stackframe/stack', () => ({ useStackApp: () => ({ sendMagicLinkEmail: state.sendEmail }), useUser: () => state.user }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ provider: state.provider, loading: state.loading, isAuthenticated: Boolean(state.user), getAccessToken: state.getAccessToken }) }));
vi.mock('@/client/sdk.gen', () => ({ createServiceKeyApiV1UserServiceKeysPost: state.createKey }));

beforeEach(() => {
    vi.clearAllMocks();
    state.user = null;
    state.provider = 'stack';
    state.loading = false;
    state.sendEmail.mockResolvedValue({ status: 'ok', data: { nonce: 'nonce' } });
    state.createKey.mockResolvedValue({ data: { service_key: 'mps_sk_test' } });
    state.getAccessToken.mockResolvedValue('stack-access-token');
});

function verifiedUser() {
    state.user = { primaryEmail: 'verified@example.com', primaryEmailVerified: true, selectedTeam: { displayName: 'My team' } };
}

describe('verified email service key onboarding', () => {
    it('emails through the existing Stack handler and does not mint a key', async () => {
        render(<GetServiceKeyPage />);
        fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'user@example.com' } });
        fireEvent.click(screen.getByRole('button', { name: 'Email me a sign-in link' }));
        await screen.findByRole('status');
        expect(state.sendEmail).toHaveBeenCalledWith('user@example.com', {
            callbackUrl: `${window.location.origin}/handler/magic-link-callback?after_auth_return_to=%2Fget-service-key`,
        });
        expect(state.createKey).not.toHaveBeenCalled();
    });

    it('requires verified email before offering key issuance', () => {
        verifiedUser();
        state.user!.primaryEmailVerified = false;
        render(<GetServiceKeyPage />);
        expect(screen.queryByRole('button', { name: 'Create service key' })).toBeNull();
        expect(screen.getByRole('link', { name: 'your Dograh account settings' }).getAttribute('href')).toBe('/handler/account-settings');
        expect(state.createKey).not.toHaveBeenCalled();
    });

    it('creates once on explicit click and displays the key for copying', async () => {
        verifiedUser();
        render(<GetServiceKeyPage />);
        expect(state.createKey).not.toHaveBeenCalled();
        const button = screen.getByRole('button', { name: 'Create service key' });
        fireEvent.click(button);
        fireEvent.click(button);
        await screen.findByLabelText('Your service key');
        expect(state.createKey).toHaveBeenCalledTimes(1);
        expect(state.createKey).toHaveBeenCalledWith({ body: { name: 'OSS Dograh provider' }, headers: { Authorization: 'Bearer stack-access-token' } });
        expect(screen.queryByRole('button', { name: 'Create service key' })).toBeNull();
        expect((screen.getByLabelText('Your service key') as HTMLInputElement).value).toBe('mps_sk_test');
    });

    it('renders API validation errors without revealing a key', async () => {
        verifiedUser();
        state.createKey.mockResolvedValue({ error: { detail: [{ msg: 'Verify your email' }] } });
        render(<GetServiceKeyPage />);
        fireEvent.click(screen.getByRole('button', { name: 'Create service key' }));
        await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Verify your email'));
        expect(screen.queryByLabelText('Your service key')).toBeNull();
    });

    it('sends OSS users to hosted onboarding without authenticating or minting', () => {
        state.provider = 'local';
        render(<GetServiceKeyPage />);
        expect(screen.getByRole('link').getAttribute('href')).toBe('https://app.dograh.com/get-service-key');
        expect(state.sendEmail).not.toHaveBeenCalled();
        expect(state.createKey).not.toHaveBeenCalled();
    });
});
