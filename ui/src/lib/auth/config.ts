import "server-only";

/**
 * Returns the auth provider. No-auth mode: always 'local'.
 */
export async function getAuthProvider(): Promise<string> {
  return 'local';
}
