import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

import { OSS_TOKEN_COOKIE, OSS_USER_COOKIE, sessionCookieOptions } from '@/lib/auth/cookies';

export async function POST(request: NextRequest) {
  const cookieStore = await cookies();
  const options = sessionCookieOptions(request, 0);

  cookieStore.set(OSS_TOKEN_COOKIE, '', options);
  cookieStore.set(OSS_USER_COOKIE, '', options);

  return NextResponse.json({ success: true });
}
