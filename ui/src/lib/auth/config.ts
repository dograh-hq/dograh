import "server-only";

import { getServerBackendUrl } from "@/lib/apiClient";

export interface StackConfig {
  projectId: string;
  publishableClientKey: string;
}

interface ResolvedAuthConfig {
  authProvider: string;
  stackConfig: StackConfig | null;
}

let cachedConfig: ResolvedAuthConfig | null = null;

/**
 * Fetches the auth configuration from the backend health endpoint and caches it.
 *
 * The backend reports the active auth provider and — when it is `stack` — the
 * public Stack client config (project id + publishable client key). The UI uses
 * these at runtime to initialize Stack Auth, so they no longer need to be baked
 * into the browser bundle at build time. Falls back to local auth on error.
 */
async function resolveAuthConfig(): Promise<ResolvedAuthConfig> {
  if (cachedConfig) {
    return cachedConfig;
  }

  try {
    const backendUrl = getServerBackendUrl();
    const res = await fetch(`${backendUrl}/api/v1/health`, {
      next: { revalidate: 300 },
    });
    if (res.ok) {
      const data = await res.json();
      const authProvider = (data.auth_provider as string) || "local";
      const stackConfig =
        authProvider === "stack" &&
        data.stack_project_id &&
        data.stack_publishable_client_key
          ? {
              projectId: data.stack_project_id as string,
              publishableClientKey:
                data.stack_publishable_client_key as string,
            }
          : null;
      cachedConfig = { authProvider, stackConfig };
      return cachedConfig;
    }
  } catch {
    // Backend not reachable — fall through without caching so we retry next request.
  }

  // Unknown (backend unreachable). Return the local fallback for THIS request but
  // do NOT cache it: caching here would pin the entire UI to local auth until a
  // container restart if the first resolution loses the startup race with the api
  // service. Leaving it uncached means the next request retries and self-heals.
  return { authProvider: "local", stackConfig: null };
}

/**
 * Returns the active auth provider ('local' or 'stack'). Falls back to 'local'.
 */
export async function getAuthProvider(): Promise<string> {
  return (await resolveAuthConfig()).authProvider;
}

/**
 * Returns the public Stack client config when the active provider is `stack`,
 * otherwise null. Server-only — the browser receives these via /api/config/auth.
 */
export async function getStackConfig(): Promise<StackConfig | null> {
  return (await resolveAuthConfig()).stackConfig;
}
