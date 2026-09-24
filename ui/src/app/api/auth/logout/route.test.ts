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
  const headers = new Headers();
  if (forwardedProto) {
    headers.set('x-forwarded-proto', forwardedProto);
  }
  return new NextRequest(url, { method: 'POST', headers });
}

describe('POST /api/auth/logout', () => {
  beforeEach(() => {
    cookieStore.set.mockClear();
    vi.unstubAllEnvs();
  });

  it('expires both session cookies', async () => {
    const response = await POST(makeRequest('https://app.dograh.com/api/auth/logout'));

    expect(response.status).toBe(200);
    expect(cookieStore.set.mock.calls.map((call) => call[0])).toEqual([
      OSS_TOKEN_COOKIE,
      OSS_USER_COOKIE,
    ]);
    for (const [, value, options] of cookieStore.set.mock.calls) {
      expect(value).toBe('');
      expect(options.maxAge).toBe(0);
    }
  });

  // A Set-Cookie only clears the original if its attributes match, so the
  // Secure flag has to be derived the same way the session route derives it.
  it('matches the Secure flag of the cookie it is clearing', async () => {
    vi.stubEnv('NODE_ENV', 'production');

    await POST(makeRequest('http://10.56.20.71/api/auth/logout'));
    expect(cookieStore.set.mock.calls[0][2].secure).toBe(false);

    cookieStore.set.mockClear();
    await POST(makeRequest('http://10.56.20.71/api/auth/logout', 'https'));
    expect(cookieStore.set.mock.calls[0][2].secure).toBe(true);
  });
});
