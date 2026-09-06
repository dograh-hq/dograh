/*
  Provides authentication token to LocalProviderWrapper once loaded
  in the browser.
  Returns 401 if no token cookie exists (user needs to log in).
*/
import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

import { getAuthProvider } from '@/lib/auth/config';
import { validateOSSSession } from '@/lib/auth/server';

const OSS_TOKEN_COOKIE = 'dograh_auth_token';
const OSS_USER_COOKIE = 'dograh_auth_user';

export async function GET() {
  const authProvider = await getAuthProvider();

  // Only handle OSS mode
  if (authProvider !== 'local') {
    return NextResponse.json({ error: 'Not in OSS mode' }, { status: 400 });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get(OSS_TOKEN_COOKIE)?.value;
  const user = cookieStore.get(OSS_USER_COOKIE)?.value;

  // A cookie is only a session candidate. Validate it with the API before the
  // browser adopts it; otherwise an expired token leaves the client looking
  // authenticated and every page request fails with a misleading API error.
  if (!token || (await validateOSSSession(token)) === false) {
    cookieStore.set(OSS_TOKEN_COOKIE, '', { maxAge: 0, path: '/' });
    cookieStore.set(OSS_USER_COOKIE, '', { maxAge: 0, path: '/' });
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 });
  }

  // Return the auth info as JSON
  return NextResponse.json({
    token,
    user: user ? JSON.parse(user) : { id: token, name: 'Local User', provider: 'local' },
  });
}
