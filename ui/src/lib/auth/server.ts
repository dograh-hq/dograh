import "server-only";

import type { CurrentUser, StackServerApp } from '@stackframe/stack';
import { cookies } from 'next/headers';

import logger from '@/lib/logger';

import type { LocalUser } from './types';

// Server-side auth utilities for SSR pages
// This file should only be imported in server components

let stackServerApp: StackServerApp<boolean, string> | null = null;
const OSS_TOKEN_COOKIE = 'dograh_oss_token';
const OSS_USER_COOKIE = 'dograh_oss_user';

// Lazy load and cache the stack server app
async function getStackServerApp(): Promise<StackServerApp<boolean, string> | null> {
  if (!stackServerApp) {
    // Only import if using Stack provider
    const authProvider = process.env.NEXT_PUBLIC_AUTH_PROVIDER || 'stack';
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
  const authProvider = process.env.NEXT_PUBLIC_AUTH_PROVIDER || 'stack';

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
 * Check if user is authenticated on the server side
 * For local provider, always returns true in development
 */
export async function isServerAuthenticated(): Promise<boolean> {
  const authProvider = process.env.NEXT_PUBLIC_AUTH_PROVIDER || 'stack';

  if (authProvider === 'stack') {
    const user = await getServerUser();
    return !!user;
  }

  // For local provider, consider authenticated in development
  if (authProvider === 'local') {
    return process.env.NODE_ENV === 'development';
  }

  return false;
}

/**
 * Get provider name for server-side rendering
 */
export function getServerAuthProvider(): string {
  return process.env.NEXT_PUBLIC_AUTH_PROVIDER || 'stack';
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
      return JSON.parse(userCookie);
    } catch (error) {
      logger.error('Error listing permissions:', error);
      return null;
    }
  }

  // If no user cookie, but we have a token, create user
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
  const authProvider = getServerAuthProvider();

  if (authProvider === 'stack') {
    const user = await getServerUser();
    if (user && 'getAuthJson' in user) {
      const auth = await user.getAuthJson();
      return auth?.accessToken ?? null;
    }
  } else if (authProvider === 'local') {
    // Get token from cookies (created by middleware)
    const oss_token = await getOSSToken();
    console.log(`[SERVER_AUTH] Server-side token: ${oss_token}`);
    logger.debug(`oss_token: ${oss_token}`);
    return oss_token;
  }

  return null;
}
