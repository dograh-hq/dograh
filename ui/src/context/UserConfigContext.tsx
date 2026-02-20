'use client';

import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';

import { client } from '@/client/client.gen';
import { getUserConfigurationsApiV1UserConfigurationsUserGet, updateUserConfigurationsApiV1UserConfigurationsUserPut } from '@/client/sdk.gen';
import type { UserConfigurationRequestResponseSchema } from '@/client/types.gen';
import { setupAuthInterceptor } from '@/lib/apiClient';
import type { AuthUser } from '@/lib/auth';
import { useAuth } from '@/lib/auth';


export type SaveUserConfigFunctionParams = {
    llm?: {
        [key: string]: string | number;
    } | null;
    tts?: {
        [key: string]: string | number;
    } | null;
    stt?: {
        [key: string]: string | number;
    } | null;
    embeddings?: {
        [key: string]: string | number;
    } | null;
    test_phone_number?: string | null;
    timezone?: string | null;
};


interface TeamPermission {
    id: string;
}

interface OrganizationPricing {
    price_per_second_usd: number | null;
    currency: string;
    billing_enabled: boolean;
}

interface UserConfigContextType {
    userConfig: UserConfigurationRequestResponseSchema | null;
    saveUserConfig: (userConfig: SaveUserConfigFunctionParams) => Promise<void>;
    loading: boolean;
    error: Error | null;
    refreshConfig: () => Promise<void>;
    permissions: TeamPermission[];
    user: AuthUser | null;
    organizationPricing: OrganizationPricing | null;
}

const UserConfigContext = createContext<UserConfigContextType | null>(null);

export function UserConfigProvider({ children }: { children: ReactNode }) {
    const [userConfig, setUserConfig] = useState<UserConfigurationRequestResponseSchema | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);
    const [organizationPricing, setOrganizationPricing] = useState<OrganizationPricing | null>(null);
    const [permissions, setPermissions] = useState<TeamPermission[]>([]);

    const auth = useAuth();

    // Store auth functions in refs to avoid dependency issues
    const authRef = useRef(auth);
    authRef.current = auth;

    // Track initialization
    const hasFetchedConfig = useRef(false);
    const hasFetchedPermissions = useRef(false);

    // Register the auth interceptor synchronously during render (not in useEffect)
    // so it's in place before any child effects fire API calls.
    // setupAuthInterceptor is idempotent — safe for strict mode double-renders.
    if (!auth.loading && auth.isAuthenticated) {
        setupAuthInterceptor(client, auth.getAccessToken);
    }

    // Fetch permissions once when auth is ready
    useEffect(() => {
        if (auth.loading || hasFetchedPermissions.current) {
            return;
        }
        hasFetchedPermissions.current = true;

        const fetchPermissions = async () => {
            const currentAuth = authRef.current;
            if (currentAuth.provider === 'stack' && currentAuth.getSelectedTeam && currentAuth.listPermissions) {
                const selectedTeam = currentAuth.getSelectedTeam();
                if (selectedTeam) {
                    try {
                        const perms = await currentAuth.listPermissions(selectedTeam);
                        setPermissions(Array.isArray(perms) ? perms : []);
                    } catch {
                        setPermissions([]);
                    }
                } else {
                    setPermissions([]);
                }
            } else {
                setPermissions([{ id: 'admin' }]);
            }
        };

        fetchPermissions();
    }, [auth.loading, auth.provider]);

    // Fetch user config once when auth is ready
    useEffect(() => {
        if (auth.loading || !auth.isAuthenticated || hasFetchedConfig.current) {
            return;
        }
        hasFetchedConfig.current = true;

        const fetchUserConfig = async () => {
            setLoading(true);
            try {
                const response = await getUserConfigurationsApiV1UserConfigurationsUserGet();

                if (response.data) {
                    setUserConfig(response.data);
                    if (response.data.organization_pricing) {
                        setOrganizationPricing({
                            price_per_second_usd: response.data.organization_pricing.price_per_second_usd as number | null,
                            currency: response.data.organization_pricing.currency as string || 'USD',
                            billing_enabled: response.data.organization_pricing.billing_enabled as boolean || false
                        });
                    } else {
                        setOrganizationPricing(null);
                    }
                }
                setError(null);
            } catch (err) {
                setError(err instanceof Error ? err : new Error('Failed to fetch user configuration'));
            } finally {
                setLoading(false);
            }
        };

        fetchUserConfig();
    }, [auth.loading, auth.isAuthenticated]);

    const saveUserConfig = useCallback(async (userConfigRequest: SaveUserConfigFunctionParams) => {
        if (!authRef.current.isAuthenticated) throw new Error('No authentication available');
        const response = await updateUserConfigurationsApiV1UserConfigurationsUserPut({
            body: {
                ...userConfig,
                ...userConfigRequest
            } as UserConfigurationRequestResponseSchema,
        });
        if (response.error) {
            let msg = 'Failed to save user configuration';
            const detail = (response.error as unknown as { detail?: { errors: { model: string; message: string }[] } }).detail;
            if (Array.isArray(detail)) {
                msg = detail
                    .map((e: { model: string; message: string }) => `${e.model}: ${e.message}`)
                    .join('\n');
            }
            throw new Error(msg);
        }
        setUserConfig(response.data!);

        if (response.data?.organization_pricing) {
            setOrganizationPricing({
                price_per_second_usd: response.data.organization_pricing.price_per_second_usd as number | null,
                currency: response.data.organization_pricing.currency as string || 'USD',
                billing_enabled: response.data.organization_pricing.billing_enabled as boolean || false
            });
        }
    }, [userConfig]);

    const refreshConfig = useCallback(async () => {
        const currentAuth = authRef.current;
        if (!currentAuth.isAuthenticated) return;

        setLoading(true);
        try {
            const response = await getUserConfigurationsApiV1UserConfigurationsUserGet();

            if (response.data) {
                setUserConfig(response.data);
                if (response.data.organization_pricing) {
                    setOrganizationPricing({
                        price_per_second_usd: response.data.organization_pricing.price_per_second_usd as number | null,
                        currency: response.data.organization_pricing.currency as string || 'USD',
                        billing_enabled: response.data.organization_pricing.billing_enabled as boolean || false
                    });
                }
            }
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err : new Error('Failed to fetch user configuration'));
        } finally {
            setLoading(false);
        }
    }, []);

    return (
        <UserConfigContext.Provider
            value={{
                userConfig,
                saveUserConfig,
                loading,
                error,
                refreshConfig,
                permissions,
                user: auth.user,
                organizationPricing,
            }}
        >
            {children}
        </UserConfigContext.Provider>
    );
}

export function useUserConfig() {
    const context = useContext(UserConfigContext);
    if (!context) {
        throw new Error('useUserConfig must be used within a UserConfigProvider');
    }
    return context;
}
