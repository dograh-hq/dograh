import { NextResponse } from 'next/server';

import { getAuthProvider } from '@/lib/auth/config';

export async function GET() {
  const provider = await getAuthProvider();
  return NextResponse.json({ provider });
}
