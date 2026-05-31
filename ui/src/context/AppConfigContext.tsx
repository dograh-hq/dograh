'use client';

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';

type BackendStatus = 'reachable' | 'unreachable';

interface AppConfig {
    uiVersion: string;
    apiVersion: string;
    deploymentMode: string;
    authProvider: string;
    turnEnabled: boolean;
    forceTurnRelay: boolean;
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

            setConfig({
                uiVersion: data.ui || 'dev',
                apiVersion: data.api || 'unknown',
                deploymentMode: data.deploymentMode || 'oss',
                authProvider: data.authProvider || 'local',
                turnEnabled: Boolean(data.turnEnabled),
                forceTurnRelay: Boolean(data.forceTurnRelay),
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
