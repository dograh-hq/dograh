import "server-only";

import type { LocalUser } from './types';

// Fixed default user for no-auth mode
const DEFAULT_USER: LocalUser = {
  id: 'default-user',
  email: 'admin@local',
  name: 'Admin',
  provider: 'local',
};

/**
 * Get the current user on the server side (for SSR).
 * No-auth mode: always returns the default user.
 */
export async function getServerUser(): Promise<LocalUser> {
  return DEFAULT_USER;
}

/**
 * Get provider name for server-side rendering.
 */
export async function getServerAuthProvider(): Promise<string> {
  return 'local';
}

/**
 * Get access token for API calls.
 * No-auth mode: returns a placeholder (backend ignores tokens entirely).
 */
export async function getServerAccessToken(): Promise<string | null> {
  return 'no-auth';
}


