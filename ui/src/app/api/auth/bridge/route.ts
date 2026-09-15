import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

const OSS_TOKEN_COOKIE = 'dograh_auth_token';
const OSS_USER_COOKIE = 'dograh_auth_user';

/**
 * GET /api/auth/bridge?token=...&user=...&redirect=...
 *
 * Auth bridge for cross-domain token handoff. Vani redirects here after
 * provisioning a Dograh account; this endpoint sets the session cookies
 * and redirects to the studio dashboard.
 */
export async function GET(request: NextRequest) {
  const token = request.nextUrl.searchParams.get('token');
  const userParam = request.nextUrl.searchParams.get('user');
  const redirect = request.nextUrl.searchParams.get('redirect') || '/after-sign-in';

  if (!token) {
    return NextResponse.json({ error: 'Missing token' }, { status: 400 });
  }

  const user = userParam
    ? JSON.parse(userParam)
    : { id: token, name: 'User', provider: 'local' };

  const cookieStore = await cookies();
  const proto = request.headers.get('x-forwarded-proto') || request.nextUrl.protocol.replace(':', '');
  const isSecure = proto === 'https';

  cookieStore.set(OSS_TOKEN_COOKIE, token, {
    httpOnly: true,
    secure: isSecure,
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 30,
    path: '/',
  });

  cookieStore.set(OSS_USER_COOKIE, JSON.stringify(user), {
    httpOnly: true,
    secure: isSecure,
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 30,
    path: '/',
  });

  // Build redirect from the Host header so it resolves to the browser-facing
  // origin (e.g. localhost:3010) instead of the container-internal 0.0.0.0.
  const host = request.headers.get('host') || request.nextUrl.host;
  const redirectUrl = new URL(redirect, `${proto}://${host}`);

  return NextResponse.redirect(redirectUrl);
}
