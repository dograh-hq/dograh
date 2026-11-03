import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

import { OSS_TOKEN_COOKIE, OSS_USER_COOKIE, sessionCookieOptions } from '@/lib/auth/cookies';

const SESSION_MAX_AGE = 60 * 60 * 24 * 30;

export async function POST(request: NextRequest) {
  const { token, user } = await request.json();

  if (!token) {
    return NextResponse.json({ error: 'Missing token' }, { status: 400 });
  }

  const cookieStore = await cookies();
  const options = sessionCookieOptions(request, SESSION_MAX_AGE);

  cookieStore.set(OSS_TOKEN_COOKIE, token, options);
  cookieStore.set(OSS_USER_COOKIE, JSON.stringify(user), options);

  return NextResponse.json({ success: true });
}
