import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ConnectPage from './page';

type StackUser = {
    primaryEmail: string;
    primaryEmailVerified: boolean;
    selectedTeam: { displayName: string } | null;
    signOut: ReturnType<typeof vi.fn>;
};

const state = vi.hoisted(() => ({
    code: 'BCDF-GHJK' as string | null,
    user: null as StackUser | null,
    replace: vi.fn(),
    sendEmail: vi.fn(),
    signInWithCode: vi.fn(),
    getRequest: vi.fn(),
    approve: vi.fn(),
    deny: vi.fn(),
}));
vi.mock('next/navigation', () => ({
    useRouter: () => ({ replace: state.replace }),
    useSearchParams: () => ({ get: () => state.code }),
}));
vi.mock('@stackframe/stack', () => ({
    useStackApp: () => ({ sendMagicLinkEmail: state.sendEmail, signInWithMagicLink: state.signInWithCode }),
    useUser: () => state.user,
}));
vi.mock('@/lib/auth', () => ({
    useAuth: () => ({ provider: 'stack', loading: false, isAuthenticated: Boolean(state.user), getAccessToken: async () => 'token' }),
}));
vi.mock('@/client/sdk.gen', () => ({
    getDograhAccountConnectRequest: state.getRequest,
    approveDograhAccountConnectRequest: state.approve,
    denyDograhAccountConnectRequest: state.deny,
}));

const pending = { user_code: 'BCDF-GHJK', install_name: 'voice.acme.dev', install_url: 'https://voice.acme.dev', status: 'pending', expires_in: 540 };

beforeEach(() => {
    vi.clearAllMocks();
    state.code = 'BCDF-GHJK';
    state.user = { primaryEmail: 'owner@acme.dev', primaryEmailVerified: true, selectedTeam: { displayName: 'Acme' }, signOut: vi.fn() };
    state.getRequest.mockResolvedValue({ data: pending });
    state.approve.mockResolvedValue({ data: { status: 'approved' } });
    state.deny.mockResolvedValue({ data: { status: 'denied' } });
    state.sendEmail.mockResolvedValue({ status: 'ok', data: { nonce: '-nonce' } });
    state.signInWithCode.mockResolvedValue({ status: 'ok', data: undefined });
});

describe('approving a connection', () => {
    it('names the signed-in account, the installation, the team and the code', async () => {
        render(<ConnectPage />);
        const heading = await screen.findByRole('heading', { name: 'Connect a self-hosted installation' });
        expect(heading.parentElement?.textContent).toContain('wants to use Dograh models with your account, owner@acme.dev.');
        expect(screen.getByText('voice.acme.dev')).toBeDefined();
        expect(screen.getByText('https://voice.acme.dev')).toBeDefined();
        expect(screen.getByText('Acme')).toBeDefined();
        expect(screen.getByText('BCDF-GHJK')).toBeDefined();
        expect(await screen.findByText('The code expires in 9 minutes.')).toBeDefined();
        expect(screen.getByRole('link', { name: 'API Keys' }).getAttribute('href')).toBe('/api-keys');
        expect(state.getRequest).toHaveBeenCalledWith({ path: { user_code: 'BCDF-GHJK' }, headers: { Authorization: 'Bearer token' } });
    });

    it('confirms an approval', async () => {
        render(<ConnectPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Approve connection' }));
        expect(await screen.findByRole('heading', { name: 'Installation connected' })).toBeDefined();
        expect(screen.getByRole('status').textContent).toContain('voice.acme.dev can now use Dograh models on Acme.');
        expect(state.approve).toHaveBeenCalledWith({ path: { user_code: 'BCDF-GHJK' }, headers: { Authorization: 'Bearer token' } });
        expect(state.deny).not.toHaveBeenCalled();
    });

    it('confirms a denial', async () => {
        render(<ConnectPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Deny' }));
        expect(await screen.findByRole('heading', { name: 'Request denied' })).toBeDefined();
        expect(state.approve).not.toHaveBeenCalled();
    });

    it('keeps the request open when a decision fails', async () => {
        state.approve.mockResolvedValue({ error: { detail: 'Too many requests' } });
        render(<ConnectPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Approve connection' }));
        expect((await screen.findByRole('alert')).textContent).toBe('Too many requests');
        expect(screen.getByRole('button', { name: 'Approve connection' })).toBeDefined();
    });

    it('does not offer approval once the code has expired', async () => {
        state.getRequest.mockResolvedValue({ data: { ...pending, expires_in: 0 } });
        render(<ConnectPage />);
        expect(await screen.findByRole('heading', { name: 'This code has expired' })).toBeDefined();
        expect(screen.queryByRole('button', { name: 'Approve connection' })).toBeNull();
    });

    it('explains a code that cannot be found', async () => {
        state.getRequest.mockResolvedValue({ error: { detail: 'Connect request not found' } });
        render(<ConnectPage />);
        expect(await screen.findByRole('heading', { name: "This code didn't work" })).toBeDefined();
        expect(screen.getByText('Connect request not found')).toBeDefined();
        expect(screen.getByRole('link', { name: 'Enter a different code' }).getAttribute('href')).toBe('/connect');
    });
});

describe('before approving', () => {
    it('formats the code as it is typed and continues once it is complete', () => {
        state.code = null;
        render(<ConnectPage />);
        const input = screen.getByLabelText('Connection code') as HTMLInputElement;
        const next = screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement;
        fireEvent.change(input, { target: { value: 'bcdf g' } });
        expect(input.value).toBe('BCDF-G');
        expect(next.disabled).toBe(true);
        fireEvent.change(input, { target: { value: 'bcdf ghjk' } });
        expect(input.value).toBe('BCDF-GHJK');
        fireEvent.click(next);
        expect(state.replace).toHaveBeenCalledWith('/connect?code=BCDF-GHJK');
    });

    it('asks a signed-out person to sign in and brings them back to this code', async () => {
        state.user = null;
        render(<ConnectPage />);
        expect(screen.getByRole('heading', { name: 'Sign in to connect' })).toBeDefined();
        fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@acme.dev' } });
        fireEvent.click(screen.getByRole('button', { name: 'Email me a sign-in link' }));
        expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeDefined();
        expect(screen.getByRole('status').textContent).toContain('We sent a sign-in link to new@acme.dev.');
        expect(state.sendEmail).toHaveBeenCalledWith('new@acme.dev', {
            callbackUrl: `${window.location.origin}/handler/magic-link-callback?after_auth_return_to=${encodeURIComponent('/connect?code=BCDF-GHJK')}`,
        });
        expect(screen.getByRole('button', { name: 'Use a different email' })).toBeDefined();
        expect(state.getRequest).not.toHaveBeenCalled();
    });

    async function emailSent() {
        state.user = null;
        render(<ConnectPage />);
        fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@acme.dev' } });
        fireEvent.click(screen.getByRole('button', { name: 'Email me a sign-in link' }));
        return await screen.findByLabelText('Code from the email') as HTMLInputElement;
    }

    it('signs in with a pasted code from the email, staying on this page', async () => {
        const input = await emailSent();
        fireEvent.change(input, { target: { value: ' ab3-x9q ' } });
        expect(input.value).toBe('AB3X9Q');
        await waitFor(() => expect(state.signInWithCode).toHaveBeenCalledWith('AB3X9Q-nonce', { noRedirect: true }));
        expect(state.signInWithCode).toHaveBeenCalledOnce();
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('clears a wrong code so it can be entered again', async () => {
        state.signInWithCode.mockResolvedValue({ status: 'error', error: new Error('Invalid code') });
        const input = await emailSent();
        fireEvent.change(input, { target: { value: 'WRONG1' } });
        expect((await screen.findByRole('alert')).textContent).toContain("That code didn't work");
        expect(input.value).toBe('');
        expect((screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement).disabled).toBe(true);
    });

    it('goes back to the email form to use a different address', async () => {
        await emailSent();
        fireEvent.click(screen.getByRole('button', { name: 'Use a different email' }));
        expect(screen.getByRole('heading', { name: 'Sign in to connect' })).toBeDefined();
        expect(state.signInWithCode).not.toHaveBeenCalled();
    });

    it('asks for a verified email before showing the request', async () => {
        state.user = { ...state.user!, primaryEmailVerified: false };
        render(<ConnectPage />);
        expect(screen.getByRole('heading', { name: 'Verify your email to continue' })).toBeDefined();
        await waitFor(() => expect(state.getRequest).not.toHaveBeenCalled());
    });
});
