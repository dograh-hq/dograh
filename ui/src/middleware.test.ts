// @vitest-environment node
import { NextRequest } from 'next/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

function requestWithToken(token = 'candidate-token') {
    return new NextRequest('http://localhost:3010/sakinah/sim', {
        headers: {
            cookie: `dograh_auth_token=${token}; dograh_auth_user=%7B%7D`,
        },
    });
}

function healthResponse() {
    return new Response(JSON.stringify({ auth_provider: 'local' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
    });
}

describe('OSS auth middleware', () => {
    beforeEach(() => {
        vi.resetModules();
        vi.unstubAllGlobals();
    });

    it('redirects and clears cookies when the API rejects the session', async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(new Response(null, { status: 401 }));
        vi.stubGlobal('fetch', fetchMock);

        const { middleware } = await import('./middleware');
        const response = await middleware(requestWithToken());

        expect(response.status).toBe(307);
        expect(response.headers.get('location')).toBe('http://localhost:3010/auth/login');
        expect(response.headers.getSetCookie().join(';')).toContain('dograh_auth_token=');
        expect(response.headers.getSetCookie().join(';')).toContain('dograh_auth_user=');
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('allows a session the API accepts', async () => {
        const fetchMock = vi.fn()
            .mockResolvedValueOnce(healthResponse())
            .mockResolvedValueOnce(new Response(JSON.stringify({ id: 7 }), { status: 200 }));
        vi.stubGlobal('fetch', fetchMock);

        const { middleware } = await import('./middleware');
        const response = await middleware(requestWithToken());

        expect(response.status).toBe(200);
        expect(response.headers.get('location')).toBeNull();
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });
});
