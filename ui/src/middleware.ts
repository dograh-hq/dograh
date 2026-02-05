import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

const OSS_TOKEN_COOKIE = 'dograh_oss_token';
const OSS_USER_COOKIE = 'dograh_oss_user';

function generateOSSToken(): string {
  return `oss_${Date.now()}_${crypto.randomUUID()}`;
}

export function middleware(request: NextRequest) {
  // Check for maintenance mode
  const maintenanceMode = process.env.MAINTENANCE_MODE === 'true';

  if (maintenanceMode) {
    // Allow access to the maintenance page itself to avoid redirect loop
    if (request.nextUrl.pathname === '/maintenance') {
      return NextResponse.next();
    }

    // Return 503 for API routes during maintenance
    if (request.nextUrl.pathname.startsWith('/api')) {
      return NextResponse.json(
        {
          error: 'Service Unavailable',
          message:
            process.env.MAINTENANCE_MESSAGE ||
            'We are currently performing scheduled maintenance. Please try again later.',
        },
        { status: 503 }
      );
    }

    // Redirect all other requests to maintenance page
    return NextResponse.redirect(new URL('/maintenance', request.url));
  }

  // Skip OSS token handling for API routes
  if (request.nextUrl.pathname.startsWith('/api')) {
    return NextResponse.next();
  }

  const authProvider = process.env.NEXT_PUBLIC_AUTH_PROVIDER || 'stack';

  // Only handle OSS mode
  if (authProvider !== 'local') {
    return NextResponse.next();
  }

  const response = NextResponse.next();
  const token = request.cookies.get(OSS_TOKEN_COOKIE)?.value;

  // If no token exists, create one
  if (!token) {
    const newToken = generateOSSToken();
    const user = {
      id: newToken,
      name: 'Local User',
      provider: 'local',
      organizationId: `org_${newToken}`,
    };

    // Set cookies in the response (httpOnly for security)
    response.cookies.set(OSS_TOKEN_COOKIE, newToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: '/',
    });

    response.cookies.set(OSS_USER_COOKIE, JSON.stringify(user), {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: '/',
    });
  }

  return response;
}

// Configure which routes the middleware runs on
export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - public files (public folder)
     */
    '/((?!_next/static|_next/image|favicon.ico|public).*)',
  ],
};
