'use client';

import { createContext, ReactNode, useContext, useEffect, useState } from 'react';

import { client } from '@/client/client.gen';

interface AppConfig {
    uiVersion: string;
    apiVersion: string;
    backendApiEndpoint: string | null;
    deploymentMode: string;
    authProvider: string;
}

interface AppConfigContextType {
    config: AppConfig | null;
    loading: boolean;
}

const defaultConfig: AppConfig = {
    uiVersion: 'dev',
    apiVersion: 'unknown',
    backendApiEndpoint: null,
    deploymentMode: 'oss',
    authProvider: 'local',
};

const AppConfigContext = createContext<AppConfigContextType>({
    config: null,
    loading: true,
});

export function AppConfigProvider({ children }: { children: ReactNode }) {
    const [config, setConfig] = useState<AppConfig | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetch('/api/config/version')
            .then((res) => res.json())
            .then((data) => {
                // Use clientApiBaseUrl (filtered for browser-reachable URLs)
                // to configure the API client; keep backendApiEndpoint for display
                if (data.clientApiBaseUrl) {
                    client.setConfig({ baseUrl: data.clientApiBaseUrl });
                }
                setConfig({
                    uiVersion: data.ui || 'dev',
                    apiVersion: data.api || 'unknown',
                    backendApiEndpoint: data.backendApiEndpoint || null,
                    deploymentMode: data.deploymentMode || 'oss',
                    authProvider: data.authProvider || 'local',
                });
            })
            .catch(() => {
                setConfig(defaultConfig);
            })
            .finally(() => {
                setLoading(false);
            });
    }, []);

    return (
        <AppConfigContext.Provider value={{ config, loading }}>
            {children}
        </AppConfigContext.Provider>
    );
}

export function useAppConfig() {
    return useContext(AppConfigContext);
}
