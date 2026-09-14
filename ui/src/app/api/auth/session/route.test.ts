// @vitest-environment node
import { NextRequest } from 'next/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const cookieStore = { set: vi.fn() };

vi.mock('next/headers', () => ({
  cookies: vi.fn(async () => cookieStore),
}));

import { OSS_TOKEN_COOKIE, OSS_USER_COOKIE } from '@/lib/auth/cookies';

import { POST } from './route';

function makeRequest(url: string, forwardedProto?: string) {
  const headers = new Headers({ 'content-type': 'application/json' });
  if (forwardedProto) {
    headers.set('x-forwarded-proto', forwardedProto);
  }
  return new NextRequest(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({ token: 'tok-1', user: { id: 'u1' } }),
  });
}

async function secureFlagFor(url: string, forwardedProto?: string) {
  await POST(makeRequest(url, forwardedProto));
  const [, , options] = cookieStore.set.mock.calls[0];
  return options.secure;
}

describe('POST /api/auth/session', () => {
  beforeEach(() => {
    cookieStore.set.mockClear();
    vi.unstubAllEnvs();
  });

  it('stores both session cookies with shared attributes', async () => {
    const response = await POST(makeRequest('https://app.dograh.com/api/auth/session'));

    expect(response.status).toBe(200);
    expect(cookieStore.set).toHaveBeenCalledTimes(2);

    const [tokenName, tokenValue, tokenOptions] = cookieStore.set.mock.calls[0];
    const [userName, userValue] = cookieStore.set.mock.calls[1];
    expect(tokenName).toBe(OSS_TOKEN_COOKIE);
    expect(tokenValue).toBe('tok-1');
    expect(userName).toBe(OSS_USER_COOKIE);
    expect(JSON.parse(userValue)).toEqual({ id: 'u1' });
    expect(tokenOptions).toMatchObject({
      httpOnly: true,
      sameSite: 'lax',
      path: '/',
      maxAge: 60 * 60 * 24 * 30,
    });
  });

  it('rejects a request with no token', async () => {
    const request = new NextRequest('http://10.56.20.71/api/auth/session', {
      method: 'POST',
      headers: new Headers({ 'content-type': 'application/json' }),
      body: JSON.stringify({ user: { id: 'u1' } }),
    });

    const response = await POST(request);

    expect(response.status).toBe(400);
    expect(cookieStore.set).not.toHaveBeenCalled();
  });

  it('marks the cookie Secure on a direct https request', async () => {
    expect(await secureFlagFor('https://app.dograh.com/api/auth/session')).toBe(true);
  });

  it('marks the cookie Secure behind a TLS-terminating proxy', async () => {
    expect(await secureFlagFor('http://10.56.20.71/api/auth/session', 'https')).toBe(true);
  });

  it('honors x-forwarded-proto case-insensitively', async () => {
    expect(await secureFlagFor('http://10.56.20.71/api/auth/session', 'HTTPS')).toBe(true);
  });

  it('reads only the first hop of a forwarded proto chain', async () => {
    expect(await secureFlagFor('http://10.56.20.71/api/auth/session', 'https, http')).toBe(true);
  });

  // The regression: a production build served over plain HTTP used to set
  // Secure from NODE_ENV, the browser dropped the cookie, and middleware
  // bounced the user back to /auth/login on the next request.
  it('omits Secure on a plain-http deployment even in a production build', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    expect(await secureFlagFor('http://10.56.20.71/api/auth/session')).toBe(false);
  });

  it('omits Secure when the proxy reports a plain-http browser leg', async () => {
    vi.stubEnv('NODE_ENV', 'production');
    expect(await secureFlagFor('http://10.56.20.71/api/auth/session', 'http')).toBe(false);
  });
});
