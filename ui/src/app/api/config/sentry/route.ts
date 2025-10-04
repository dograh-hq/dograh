import { NextResponse } from 'next/server';

export async function GET() {
  return NextResponse.json({
    enabled: process.env.ENABLE_TELEMETRY === 'true',
    dsn: process.env.SENTRY_DSN || '',
    environment: process.env.NODE_ENV || 'development',
  });
}
