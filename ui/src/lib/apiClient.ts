import type { CreateClientConfig } from '@/client/client.gen';
import { getBackendUrl } from '@/lib/backend-url';

export const createClientConfig: CreateClientConfig = (config) => {
    let baseUrl = getBackendUrl()
    const isServerSide = typeof window === 'undefined';
    console.log(`[${isServerSide ? 'SSR' : 'CSR'}] Backend URL: ${baseUrl}`);
    return {
        ...config,
        baseUrl: baseUrl,
    };
};
