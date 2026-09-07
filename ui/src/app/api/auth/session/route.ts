import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

import { validateOSSSession } from '@/lib/auth/server';

const OSS_TOKEN_COOKIE = 'dograh_auth_token';
const OSS_USER_COOKIE = 'dograh_auth_user';

export async function POST(request: NextRequest) {
  const { token, user } = await request.json();

  if (!token) {
    return NextResponse.json({ error: 'Missing token' }, { status: 400 });
  }

  // Do not install a browser session unless the local API that owns the JWT
  // signing secret accepts it. This prevents stale/invalid cookies from being
  // captured by the UI after a failed login or a restarted local stack.
  const valid = await validateOSSSession(token);
  if (valid === false) {
    return NextResponse.json({ error: 'Invalid or expired token' }, { status: 401 });
  }
  if (valid === null) {
    return NextResponse.json({ error: 'Authentication service unavailable' }, { status: 503 });
  }

  const cookieStore = await cookies();
  // Next production builds set NODE_ENV to "production" even for the local
  // OSS container. Derive Secure from the actual browser request so Safari
  // can accept localhost HTTP cookies while HTTPS deployments remain secure.
  const secureCookie = request.nextUrl.protocol === 'https:';

  cookieStore.set(OSS_TOKEN_COOKIE, token, {
    httpOnly: true,
    secure: secureCookie,
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 30,
    path: '/',
  });

  cookieStore.set(OSS_USER_COOKIE, JSON.stringify(user), {
    httpOnly: true,
    secure: secureCookie,
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 30,
    path: '/',
  });

  return NextResponse.json({ success: true });
}
