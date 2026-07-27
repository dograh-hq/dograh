import { NextResponse } from 'next/server';

export async function GET() {
  const supportDiagnosticsSetting =
    process.env.ENABLE_SUPPORT_DIAGNOSTICS ??
    process.env.ENABLE_TELEMETRY ??
    'true';
  const supportDiagnosticsEnabled =
    supportDiagnosticsSetting.toLowerCase() === 'true';

  return NextResponse.json({
    enabled: supportDiagnosticsEnabled,
    dsn: process.env.SENTRY_DSN || '',
    environment: process.env.NODE_ENV || 'development',
  });
}
