import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

import { getServerBackendUrl } from '@/lib/apiClient';

const OSS_TOKEN_COOKIE = 'dograh_auth_token';

// Paths that don't require authentication in OSS mode
const PUBLIC_PATHS = ['/auth/login', '/auth/signup'];

interface CachedServerConfig {
  authProvider: string;
  signupEnabled: boolean;
}

// Cache the /health lookup for a short window so we don't hammer the api on
// every request, but do refresh so an operator flipping ENABLE_SIGNUP (or
// AUTH_PROVIDER) at runtime propagates without a full UI-pod restart. Five
// minutes matches the same interval used by lib/auth/config.ts.
const SERVER_CONFIG_TTL_MS = 5 * 60 * 1000;

let cachedServerConfig: CachedServerConfig | null = null;
let cachedServerConfigExpiresAt = 0;

async function fetchServerConfig(): Promise<CachedServerConfig> {
  if (cachedServerConfig && Date.now() < cachedServerConfigExpiresAt) {
    return cachedServerConfig;
  }

  try {
    const backendUrl = getServerBackendUrl();
    const res = await fetch(`${backendUrl}/api/v1/health`);
    if (res.ok) {
      const data = await res.json();
      // Only cache a DEFINITIVE answer from the backend. Never cache a failure:
      // a single early request during container startup (before the api service
      // is reachable) would otherwise poison the cache and redirect every Stack
      // user to the local /auth/login form even though the backend reports
      // `stack`.
      cachedServerConfig = {
        authProvider: (data.auth_provider as string) || 'local',
        // Default to signup-enabled when the field is absent (older backend
        // versions or a startup race) — matches the backend's own default.
        signupEnabled: data.signup_enabled !== false,
      };
      cachedServerConfigExpiresAt = Date.now() + SERVER_CONFIG_TTL_MS;
      return cachedServerConfig;
    }
  } catch {
    // Backend not reachable — fall through without caching so we retry next request.
  }

  // Provider unknown (backend unreachable). Return a non-'local' sentinel so the
  // middleware does NOT guard/redirect: assuming 'local' here would bounce Stack
  // users to /auth/login. Deliberately not cached — the next request retries.
  return { authProvider: 'unknown', signupEnabled: true };
}

export async function middleware(request: NextRequest) {
  const { authProvider, signupEnabled } = await fetchServerConfig();

  // When the operator has disabled signup (ENABLE_SIGNUP=false), bounce every
  // /auth/signup hit to /auth/login *before* Next.js gets a chance to serve a
  // prerendered signup page. Backend still enforces the 403 — this is UI polish.
  if (
    !signupEnabled &&
    request.nextUrl.pathname.startsWith('/auth/signup')
  ) {
    return NextResponse.redirect(new URL('/auth/login', request.url));
  }

  // Only handle OSS mode
  if (authProvider !== 'local') {
    return NextResponse.next();
  }

  const token = request.cookies.get(OSS_TOKEN_COOKIE)?.value;
  const { pathname } = request.nextUrl;

  // Allow public paths without auth
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // If no token, redirect to login
  if (!token) {
    const loginUrl = new URL('/auth/login', request.url);
    return NextResponse.redirect(loginUrl);
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
     */
    '/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|otf)).*)',
  ],
};
