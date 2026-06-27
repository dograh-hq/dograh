'use client';

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';

import { client } from '@/client/client.gen';
import { resolveBrowserBackendUrl } from '@/lib/apiClient';

type BackendStatus = 'reachable' | 'unreachable';

interface AppConfig {
    uiVersion: string;
    apiVersion: string;
    deploymentMode: string;
    authProvider: string;
    turnEnabled: boolean;
    forceTurnRelay: boolean;
    // Public URL when the deployment is reached through a Cloudflare tunnel
    // (host has no public IP); null for a directly-reachable deployment.
    tunnelUrl: string | null;
    // The URL the backend reports it is running on (via /health). This is the
    // address the browser reaches the backend at — a private IP when the backend
    // runs on one. null until /health is reached. Used to resolve the API client
    // base URL; distinct from tunnelUrl, which is only for external consumers.
    backendApiEndpoint: string | null;
    backendStatus: BackendStatus;
    backendUrl: string;
    backendMessage: string | null;
}

interface AppConfigContextType {
    config: AppConfig | null;
    loading: boolean;
    refresh: () => Promise<void>;
}

const defaultConfig: AppConfig = {
    uiVersion: 'dev',
    apiVersion: 'unavailable',
    deploymentMode: 'oss',
    authProvider: 'local',
    turnEnabled: false,
    forceTurnRelay: false,
    tunnelUrl: null,
    backendApiEndpoint: null,
    backendStatus: 'unreachable',
    backendUrl: process.env.NEXT_PUBLIC_BACKEND_URL || 'unknown',
    backendMessage: process.env.NEXT_PUBLIC_BACKEND_URL
        ? `Unable to verify backend health at ${process.env.NEXT_PUBLIC_BACKEND_URL}.`
        : 'Unable to verify backend health.',
};

const AppConfigContext = createContext<AppConfigContextType>({
    config: null,
    loading: true,
    refresh: async () => { },
});

export function AppConfigProvider({ children }: { children: ReactNode }) {
    const [config, setConfig] = useState<AppConfig | null>(null);
    const [loading, setLoading] = useState(true);

    const loadConfig = useCallback(async () => {
        setLoading(true);
        try {
            const response = await fetch('/api/config/version', { cache: 'no-store' });
            const data = await response.json();
            const backend = data.backend && typeof data.backend === 'object' ? data.backend : {};
            const backendStatus: BackendStatus = backend.status === 'reachable' ? 'reachable' : 'unreachable';
            const backendUrl = typeof backend.url === 'string' && backend.url.length > 0
                ? backend.url
                : defaultConfig.backendUrl;
            const backendApiEndpoint = typeof data.backendApiEndpoint === 'string' && data.backendApiEndpoint.length > 0
                ? data.backendApiEndpoint
                : null;

            // createClientConfig seeds the API client base URL before /health is
            // known. Now that the backend has reported the endpoint it runs on,
            // re-apply the single browser→API preference order so all SDK calls
            // (and anything reading client.getConfig().baseUrl) hit it directly —
            // window.location.origin would be wrong when the API is served from a
            // different host/port. resolveBrowserBackendUrl keeps NEXT_PUBLIC_BACKEND_URL
            // ahead of the reported endpoint. Guard on a present endpoint so a
            // transient /health failure never downgrades a good base URL to origin.
            if (backendApiEndpoint) {
                client.setConfig({ baseUrl: resolveBrowserBackendUrl(backendApiEndpoint) });
            }

            setConfig({
                uiVersion: data.ui || 'dev',
                apiVersion: data.api || 'unknown',
                deploymentMode: data.deploymentMode || 'oss',
                authProvider: data.authProvider || 'local',
                turnEnabled: Boolean(data.turnEnabled),
                forceTurnRelay: Boolean(data.forceTurnRelay),
                tunnelUrl: typeof data.tunnelUrl === 'string' ? data.tunnelUrl : null,
                backendApiEndpoint,
                backendStatus,
                backendUrl,
                backendMessage: typeof backend.message === 'string' && backend.message.length > 0
                    ? backend.message
                    : backendStatus === 'reachable'
                        ? null
                        : `Backend is not reachable at ${backendUrl}.`,
            });
        } catch {
            setConfig(defaultConfig);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadConfig();
    }, [loadConfig]);

    return (
        <AppConfigContext.Provider value={{ config, loading, refresh: loadConfig }}>
            {children}
        </AppConfigContext.Provider>
    );
}

export function useAppConfig() {
    return useContext(AppConfigContext);
}
