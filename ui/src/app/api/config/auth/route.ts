import { NextResponse } from 'next/server';

import { getAuthProvider, getStackConfig } from '@/lib/auth/config';
import logger from '@/lib/logger';

export async function GET() {
  const provider = await getAuthProvider();
  // When using Stack, hand the public client config to the browser so it can
  // initialize the Stack SDK at runtime (no build-time NEXT_PUBLIC_* needed).
  const stackConfig = provider === 'stack' ? await getStackConfig() : null;
  logger.debug(`Got provider ${provider} from getAuthProvider`)
  return NextResponse.json({
    provider,
    stackProjectId: stackConfig?.projectId ?? null,
    stackPublishableClientKey: stackConfig?.publishableClientKey ?? null,
  });
}
