// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest';

const authState = vi.hoisted(() => ({
    cookieValues: new Map<string, string>(),
    setCookie: vi.fn(),
    validateSession: vi.fn(),
}));

vi.mock('next/headers', () => ({
    cookies: vi.fn(async () => ({
        get: (name: string) => {
            const value = authState.cookieValues.get(name);
            return value === undefined ? undefined : { value };
        },
        set: authState.setCookie,
    })),
}));

vi.mock('@/lib/auth/config', () => ({
    getAuthProvider: vi.fn(async () => 'local'),
}));

vi.mock('@/lib/auth/server', () => ({
    validateOSSSession: authState.validateSession,
}));

import { GET } from './route';

describe('GET /api/auth/oss', () => {
    beforeEach(() => {
        authState.cookieValues = new Map([
            ['dograh_auth_token', 'candidate-token'],
            ['dograh_auth_user', JSON.stringify({ id: 7, email: 'user@example.com' })],
        ]);
        authState.setCookie.mockClear();
        authState.validateSession.mockReset();
        authState.validateSession.mockResolvedValue(true);
    });

    it('returns the current session only after API validation', async () => {
        const response = await GET();

        expect(response.status).toBe(200);
        expect(await response.json()).toEqual({
            token: 'candidate-token',
            user: { id: 7, email: 'user@example.com' },
        });
        expect(authState.validateSession).toHaveBeenCalledWith('candidate-token');
        expect(authState.setCookie).not.toHaveBeenCalled();
    });

    it('clears an expired session and returns controlled 401', async () => {
        authState.validateSession.mockResolvedValue(false);

        const response = await GET();

        expect(response.status).toBe(401);
        expect(await response.json()).toEqual({ error: 'Not authenticated' });
        expect(authState.setCookie).toHaveBeenCalledTimes(2);
        expect(authState.setCookie).toHaveBeenNthCalledWith(
            1,
            'dograh_auth_token',
            '',
            { maxAge: 0, path: '/' },
        );
        expect(authState.setCookie).toHaveBeenNthCalledWith(
            2,
            'dograh_auth_user',
            '',
            { maxAge: 0, path: '/' },
        );
    });

    it('returns controlled 401 when no session cookie exists', async () => {
        authState.cookieValues.delete('dograh_auth_token');

        const response = await GET();

        expect(response.status).toBe(401);
        expect(authState.validateSession).not.toHaveBeenCalled();
    });
});
