import "server-only";

import type { CurrentUser, StackServerApp } from '@stackframe/stack';
import { cookies } from 'next/headers';

import logger from '@/lib/logger';

import { getAuthProvider } from './config';
import type { LocalUser } from './types';

// Server-side auth utilities for SSR pages
// This file should only be imported in server components

let stackServerApp: StackServerApp<boolean, string> | null = null;
const OSS_TOKEN_COOKIE = 'dograh_auth_token';
const OSS_USER_COOKIE = 'dograh_auth_user';

// Lazy load and cache the stack server app
export async function getStackServerApp(): Promise<StackServerApp<boolean, string> | null> {
  if (!stackServerApp) {
    // Only import if using Stack provider
    const authProvider = await getAuthProvider();
    if (authProvider === 'stack') {
      const stackModule = await import('@stackframe/stack');
      const { StackServerApp } = stackModule;
      stackServerApp = new StackServerApp({
        tokenStore: "nextjs-cookie",
        urls: {
          afterSignIn: "/after-sign-in"
        }
      });
    }
  }
  return stackServerApp;
}

/**
 * Get the current user on the server side (for SSR)
 * Returns CurrentUser for stack, LocalUser for OSS, or null if not authenticated
 */
export async function getServerUser(): Promise<CurrentUser | LocalUser | null> {
  const authProvider = await getAuthProvider();

  if (authProvider === 'stack') {
    const app = await getStackServerApp();
    if (app) {
      try {
        const user = await app.getUser();
        return user;
      } catch (error) {
        logger.error('Error getting user from Stack:', error);
        return null;
      }
    }
  } else if (authProvider === 'local') {
    // For OSS mode, get user from cookies (created by middleware)
    const user = await getOSSUser();
    return user;
  }

  return null;
}


/**
 * Get provider name for server-side rendering
 */
export async function getServerAuthProvider(): Promise<string> {
  return getAuthProvider();
}

/**
 * Get OSS token from cookies (read-only)
 * Token creation happens in middleware
 */
export async function getOSSToken(): Promise<string | null> {
  const cookieStore = await cookies();
  return cookieStore.get(OSS_TOKEN_COOKIE)?.value || null;
}

/**
 * Get OSS user from cookies
 */
export async function getOSSUser(): Promise<LocalUser | null> {
  const cookieStore = await cookies();
  const userCookie = cookieStore.get(OSS_USER_COOKIE)?.value;

  if (userCookie) {
    try {
      const parsed = JSON.parse(userCookie);
      // Handle both legacy format and new JWT format
      return {
        id: String(parsed.id),
        name: parsed.name || parsed.email || 'Local User',
        email: parsed.email,
        provider: 'local',
        organizationId: parsed.organizationId || (parsed.organization_id ? String(parsed.organization_id) : undefined),
      };
    } catch (error) {
      logger.error('Error parsing user cookie:', error);
      return null;
    }
  }

  // If no user cookie, but we have a token, create user from token
  const token = cookieStore.get(OSS_TOKEN_COOKIE)?.value;
  if (token) {
    const user: LocalUser = {
      id: token,
      name: 'Local User',
      provider: 'local',
      organizationId: `org_${token}`,
    };
    return user;
  }

  return null;
}

/**
 * Get access token for API calls
 */
export async function getServerAccessToken(): Promise<string | null> {
  const authProvider = await getServerAuthProvider();

  if (authProvider === 'stack') {
    const user = await getServerUser();
    if (user && 'getAuthJson' in user) {
      const auth = await user.getAuthJson();
      return auth?.accessToken ?? null;
    }
  } else if (authProvider === 'local') {
    // Get token from cookies (created by middleware)
    const oss_token = await getOSSToken();
    logger.debug(`oss_token: ${oss_token}`);
    return oss_token;
  }

  return null;
}
