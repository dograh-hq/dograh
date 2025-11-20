import type { CreateClientConfig } from '@/client/client.gen';

export const createClientConfig: CreateClientConfig = (config) => {
    // Use different URLs for server-side vs client-side
    const isServer = typeof window === 'undefined';
    let baseUrl: string;

    if (isServer) {
        // for server-side rendering, still use environment variable as fallback
        baseUrl = process.env.BACKEND_URL || 'http://api:8000';
    } else {
        // for client-side, use the current browser URL's origin
        baseUrl = process.env.NEXT_PUBLIC_BACKEND_URL || window.location.origin;
    }

    return {
        ...config,
        baseUrl,
    };
};
