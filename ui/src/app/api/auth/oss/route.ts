/*
  Helps provide authentication token to LocalAuthService once its loaded
  in the browser
*/
import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

const OSS_TOKEN_COOKIE = 'dograh_oss_token';
const OSS_USER_COOKIE = 'dograh_oss_user';

function generateOSSToken(): string {
  return `oss_${Date.now()}_${crypto.randomUUID()}`;
}

export async function GET() {
  const authProvider = process.env.NEXT_PUBLIC_AUTH_PROVIDER || 'stack';

  // Only handle OSS mode
  if (authProvider !== 'local') {
    return NextResponse.json({ error: 'Not in OSS mode' }, { status: 400 });
  }

  const cookieStore = await cookies();
  let token = cookieStore.get(OSS_TOKEN_COOKIE)?.value;
  let user = cookieStore.get(OSS_USER_COOKIE)?.value;

  // If no token exists, create one
  if (!token) {
    token = generateOSSToken();
    console.log(`[OSS_AUTH] Creating new token: ${token}`);
    user = JSON.stringify({
      id: token,
      name: 'Local User',
      provider: 'local',
      organizationId: `org_${token}`,
    });

    // Set cookies
    cookieStore.set(OSS_TOKEN_COOKIE, token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: '/',
    });

    cookieStore.set(OSS_USER_COOKIE, user, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: '/',
    });
  }

  // Return the auth info as JSON (safe to expose to client)
  return NextResponse.json({
    token,
    user: JSON.parse(user!),
  });
}
