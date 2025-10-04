/*
  Route to enable/ disable posthog from a NextJS backend route,
  rather than NEXT_PUBLIC_* keys, since NEXT_PUBLIC_* keys are
  injected during build time, and we need to provide the option
  to OSS users to disable telemetry from docker-compose.yaml
*/
import { NextResponse } from 'next/server';

export async function GET() {
  return NextResponse.json({
    enabled: process.env.ENABLE_TELEMETRY === 'true',
    key: process.env.POSTHOG_KEY || '',
    host: process.env.POSTHOG_HOST || '/ingest',
    uiHost: process.env.POSTHOG_UI_HOST || 'https://us.posthog.com',
  });
}
