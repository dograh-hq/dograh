import type { NextRequest } from 'next/server';

export const OSS_TOKEN_COOKIE = 'dograh_auth_token';
export const OSS_USER_COOKIE = 'dograh_auth_user';

/**
 * Whether the browser reached this deployment over HTTPS.
 *
 * Behind a TLS-terminating proxy the request Next receives is plain HTTP, so
 * the proxy's X-Forwarded-Proto is the only signal that the browser leg was
 * secure. Testing the request's own protocol first keeps a direct HTTPS
 * request secure even if a client spoofs the header. Same derivation as
 * src/app/impersonate/route.ts, which this shares to stop the two drifting.
 */
export function isSecureRequest(request: NextRequest): boolean {
  const forwardedProto = request.headers
    .get('x-forwarded-proto')
    ?.split(',')[0]
    ?.trim()
    .toLowerCase();

  return request.nextUrl.protocol === 'https:' || forwardedProto === 'https';
}

/**
 * Attributes for the OSS session cookies.
 *
 * `secure` is derived from the request rather than from NODE_ENV. Browsers
 * refuse to store -- or clear -- a Secure cookie on a plain-HTTP origin, so a
 * production build served over HTTP (the default for a self-hosted install
 * reached by LAN IP or bare hostname) would set the cookie, have the browser
 * silently drop it, and then bounce the user back to /auth/login on the very
 * next request because middleware sees no token.
 *
 * Clearing a cookie has to repeat the attributes it was set with, Secure
 * included, so logout derives the flag the same way.
 */
export function sessionCookieOptions(request: NextRequest, maxAge: number) {
  return {
    httpOnly: true,
    secure: isSecureRequest(request),
    sameSite: 'lax' as const,
    maxAge,
    path: '/',
  };
}
