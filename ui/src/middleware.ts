import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

import { getServerBackendUrl } from '@/lib/apiClient';

const OSS_TOKEN_COOKIE = 'dograh_auth_token';
const OSS_USER_COOKIE = 'dograh_auth_user';

// Paths that don't require authentication in OSS mode.
// `/embed` serves the public website widget (e.g. /embed/dograh-widget.js),
// which must be fetchable without a session cookie so third-party sites can
// embed it — otherwise the middleware 307-redirects the asset to /auth/login.
const PUBLIC_PATHS = ['/auth/login', '/auth/signup', '/embed', '/_avatarkit'];

let cachedAuthProvider: string | null = null;

async function fetchAuthProvider(): Promise<string> {
  if (cachedAuthProvider) {
    return cachedAuthProvider;
  }

  try {
    const backendUrl = getServerBackendUrl();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2000);
    const res = await fetch(`${backendUrl}/api/v1/health`, {
      signal: controller.signal,
    }).finally(() => clearTimeout(timeout));
    if (res.ok) {
      const data = await res.json();
      // Only cache a DEFINITIVE answer from the backend. Never cache a failure:
      // this is a module-scoped cache with no TTL, so a single early request
      // during container startup (before the api service is reachable) would
      // otherwise poison it to 'local' for the life of the worker — redirecting
      // every Stack user to the local /auth/login form even though the backend
      // reports `stack`.
      cachedAuthProvider = (data.auth_provider as string) || 'local';
      return cachedAuthProvider;
    }
  } catch {
    // Backend not reachable — fall through without caching so we retry next request.
  }

  // Provider unknown (backend unreachable). Return a non-'local' sentinel so the
  // middleware does NOT guard/redirect: assuming 'local' here would bounce Stack
  // users to /auth/login. Deliberately not cached — the next request retries.
  return 'unknown';
}

async function validateOSSSession(token: string): Promise<boolean | null> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2000);
    const response = await fetch(`${getServerBackendUrl()}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
      signal: controller.signal,
    }).finally(() => clearTimeout(timeout));
    if (response.status === 401) return false;
    return response.ok ? true : null;
  } catch {
    return null;
  }
}

function redirectToLogin(request: NextRequest) {
  const loginUrl = new URL('/auth/login', request.url);
  const response = NextResponse.redirect(loginUrl);
  response.cookies.delete(OSS_TOKEN_COOKIE);
  response.cookies.delete(OSS_USER_COOKIE);
  return response;
}

export async function middleware(request: NextRequest) {
  const authProvider = await fetchAuthProvider();

  // Only handle OSS mode
  if (authProvider !== 'local') {
    return NextResponse.next();
  }

  const token = request.cookies.get(OSS_TOKEN_COOKIE)?.value;
  const { pathname } = request.nextUrl;

  // Allow public paths without auth. Match on a path-segment boundary (exact
  // match or a `/`-delimited subpath) rather than a bare prefix, so a public
  // entry like `/embed` exempts `/embed` and `/embed/...` but NOT sibling
  // routes such as `/embed-admin` — a bare startsWith would let those bypass
  // authentication.
  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  // If no token, redirect to login
  if (!token) {
    return redirectToLogin(request);
  }

  // Do not treat cookie presence as authentication. The API is the authority
  // for token expiry and signing-secret validation. If it is temporarily
  // unreachable, continue and let the normal backend-status UI handle it.
  if ((await validateOSSSession(token)) === false) {
    return redirectToLogin(request);
  }

  return NextResponse.next();
}

// Configure which routes the middleware runs on
export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - api routes
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - public static assets (anything with a file extension, e.g. /dograh-logo.png)
     * - .wasm assets (e.g. the avatarkit WASM under /_avatarkit/*.wasm) — these
     *   are fetched by the public embed with no auth cookie, so the middleware
     *   must not 307-redirect them to /auth/login (the browser would then load
     *   the HTML login page as the WASM binary and fail with a magic-word error).
     */
    '/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|otf|wasm)).*)',
  ],
};
