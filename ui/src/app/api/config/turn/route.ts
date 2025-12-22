/*
  Route to provide TURN server configuration at runtime.
  This allows OSS users to configure TURN servers via docker-compose.yaml
  environment variables, since NEXT_PUBLIC_* keys are injected at build time.
*/
import { NextResponse } from 'next/server';

export async function GET() {
  const host = process.env.TURN_HOST || '';
  const username = process.env.TURN_USERNAME || '';
  const password = process.env.TURN_PASSWORD || '';

  // Only return enabled: true if all required fields are set
  const enabled = !!(host && username && password);

  return NextResponse.json({
    enabled,
    host,
    username,
    password,
  });
}
