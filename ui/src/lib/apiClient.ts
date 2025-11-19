import type { CreateClientConfig } from '@/client/client.gen';
import { getBackendUrl } from '@/lib/backend-url';

export const createClientConfig: CreateClientConfig = (config) => {
    let baseUrl = getBackendUrl()
    const isServerSide = typeof window === 'undefined';
    console.log(`[${isServerSide ? 'SSR' : 'CSR'}] API Client Config - Backend URL: ${baseUrl}`);
    console.log(`[${isServerSide ? 'SSR' : 'CSR'}] API Client Config - Input config:`, JSON.stringify(config, null, 2));
    
    const finalConfig = {
        ...config,
        baseUrl: baseUrl,
    };
    
    console.log(`[${isServerSide ? 'SSR' : 'CSR'}] API Client Config - Final config:`, JSON.stringify(finalConfig, null, 2));
    return finalConfig;
};
