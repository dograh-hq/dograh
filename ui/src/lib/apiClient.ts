import type { Client } from '@/client/client';
import type { CreateClientConfig } from '@/client/client.gen';

export const createClientConfig: CreateClientConfig = (config) => {
    // Use different URLs for server-side vs client-side
    const isServer = typeof window === 'undefined';
    let baseUrl: string;

    if (isServer) {
        baseUrl = process.env.BACKEND_URL || 'http://api:8000';
    } else {
        baseUrl = process.env.NEXT_PUBLIC_BACKEND_URL || window.location.origin;
    }

    return {
        ...config,
        baseUrl,
    };
};

let interceptorRegistered = false;

/**
 * Register a request interceptor that attaches a fresh access token
 * to every outgoing SDK request. Idempotent — safe for React strict mode.
 */
export function setupAuthInterceptor(apiClient: Client, getAccessToken: () => Promise<string>) {
    if (interceptorRegistered) return;
    interceptorRegistered = true;

    apiClient.interceptors.request.use(async (request) => {
        if (request.headers.get('Authorization')) {
            return request;
        }
        try {
            const token = await getAccessToken();
            request.headers.set('Authorization', `Bearer ${token}`);
        } catch {
            // If token retrieval fails, let the request proceed without auth
        }
        return request;
    });
}
