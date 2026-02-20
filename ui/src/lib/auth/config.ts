import "server-only";

let cachedAuthProvider: string | null = null;

/**
 * Fetches the auth provider from the backend health endpoint and caches it.
 * Falls back to 'local' on error.
 */
export async function getAuthProvider(): Promise<string> {
  if (cachedAuthProvider) {
    return cachedAuthProvider;
  }

  try {
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
    const res = await fetch(`${backendUrl}/api/v1/health`, {
      next: { revalidate: 300 },
    });
    if (res.ok) {
      const data = await res.json();
      cachedAuthProvider = (data.auth_provider as string) || "local";
      return cachedAuthProvider;
    }
  } catch {
    // Backend not reachable — fall back to local
  }

  cachedAuthProvider = "local";
  return cachedAuthProvider;
}
