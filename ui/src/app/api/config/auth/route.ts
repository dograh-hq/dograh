import { NextResponse } from 'next/server';

import { getAuthProvider } from '@/lib/auth/config';
import logger from '@/lib/logger';

export async function GET() {
  const provider = await getAuthProvider();
  logger.debug(`Got provider ${provider} from getAuthProvider`)
  return NextResponse.json({ provider });
}
