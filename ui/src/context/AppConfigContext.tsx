'use client';

import { createContext, ReactNode, useContext, useEffect, useState } from 'react';

import { client } from '@/client/client.gen';

const INTERNAL_HOST_RE = /^https?:\/\/(localhost|127\.0\.0\.1|api)(:\d+)?(\/|$)/;

function isInternalUrl(url: string | undefined | null): boolean {
    return !url || INTERNAL_HOST_RE.test(url);
}

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
        const browserOrigin = window.location.origin;
        fetch('/api/config/version', { cache: 'no-store' })
            .then((res) => res.json())
            .then((data) => {
                const clientApiBaseUrl = isInternalUrl(data.clientApiBaseUrl)
                    ? browserOrigin
                    : data.clientApiBaseUrl;
                const backendApiEndpoint = isInternalUrl(data.backendApiEndpoint)
                    ? browserOrigin
                    : data.backendApiEndpoint;

                client.setConfig({ baseUrl: clientApiBaseUrl });
                setConfig({
                    uiVersion: data.ui || 'dev',
                    apiVersion: data.api || 'unknown',
                    backendApiEndpoint,
                    deploymentMode: data.deploymentMode || 'oss',
                    authProvider: data.authProvider || 'local',
                });
            })
            .catch(() => {
                client.setConfig({ baseUrl: browserOrigin });
                setConfig({
                    ...defaultConfig,
                    backendApiEndpoint: browserOrigin,
                });
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
