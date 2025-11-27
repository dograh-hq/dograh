'use client';

import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';

import { getUserConfigurationsApiV1UserConfigurationsUserGet, updateUserConfigurationsApiV1UserConfigurationsUserPut } from '@/client/sdk.gen';
import type { UserConfigurationRequestResponseSchema } from '@/client/types.gen';
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
    accessToken: string | null;
    user: AuthUser | null;  // Now properly typed as CurrentUser | LocalUser
    organizationPricing: OrganizationPricing | null;
}

const UserConfigContext = createContext<UserConfigContextType | null>(null);

export function UserConfigProvider({ children }: { children: ReactNode }) {
    const [userConfig, setUserConfig] = useState<UserConfigurationRequestResponseSchema | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);
    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [organizationPricing, setOrganizationPricing] = useState<OrganizationPricing | null>(null);
    const auth = useAuth();
    const [permissions, setPermissions] = useState<TeamPermission[]>([]);


    useEffect(() => {
        const fetchPermissions = async () => {
            if (auth.provider === 'stack') {
                const selectedTeam = auth.getSelectedTeam();
                if (selectedTeam) {
                    try {
                        const perms = await auth.listPermissions(selectedTeam);
                        setPermissions(Array.isArray(perms) ? perms : []);
                    } catch {
                        setPermissions([]);
                    }
                } else {
                    setPermissions([]);
                }
            } else {
                // For non-Stack providers, set default permissions
                setPermissions([{ id: 'admin' }]);
            }
        };

        if (!auth.loading) {
            fetchPermissions();
        }
    }, [auth.loading, auth.provider, auth.getSelectedTeam, auth.listPermissions]);


    const saveUserConfig = useCallback(async (userConfigRequest: SaveUserConfigFunctionParams) => {
        if (!accessToken) throw new Error('No authentication token available');
        const response = await updateUserConfigurationsApiV1UserConfigurationsUserPut({
            body: {
                ...userConfig,
                ...userConfigRequest
            } as UserConfigurationRequestResponseSchema,
            headers: { 'Authorization': `Bearer ${accessToken}` },
        });
        if (response.error) {
            // Try to pull out a JSON array of { model, message } from response.error.detail
            let msg = 'Failed to save user configuration';
            const detail = (response.error as unknown as { detail?: { errors: { model: string; message: string }[] } }).detail;
            if (Array.isArray(detail)) {
                // Map each entry to "model: message" and join with \n
                msg = detail
                    .map((e: { model: string; message: string }) => `${e.model}: ${e.message}`)
                    .join('\n');
            }
            throw new Error(msg);
        }
        setUserConfig(response.data!);

        // Update organization pricing if available
        if (response.data?.organization_pricing) {
            setOrganizationPricing({
                price_per_second_usd: response.data.organization_pricing.price_per_second_usd as number | null,
                currency: response.data.organization_pricing.currency as string || 'USD',
                billing_enabled: response.data.organization_pricing.billing_enabled as boolean || false
            });
        }
    }, [accessToken, userConfig]);

    const fetchUserConfig = useCallback(async () => {
        setLoading(true);
        try {
            if (auth.loading || !auth.isAuthenticated) return;
            const token = await auth.getAccessToken();
            setAccessToken(token); // Set token when fetching config

            const response = await getUserConfigurationsApiV1UserConfigurationsUserGet({
                headers: {
                    'Authorization': `Bearer ${token}`,
                },
            });

            if (response.data) {
                setUserConfig(response.data);

                // Extract organization pricing if available
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
            setAccessToken(null);
        } finally {
            setLoading(false);
        }
    }, [auth.loading, auth.isAuthenticated, auth.getAccessToken]);

    useEffect(() => {
        if (!auth.loading && auth.isAuthenticated) {
            fetchUserConfig();
        }
    }, [fetchUserConfig, auth.loading, auth.isAuthenticated]);

    return (
        <UserConfigContext.Provider
            value={{
                userConfig,
                saveUserConfig,
                loading,
                error,
                refreshConfig: fetchUserConfig,
                permissions,
                accessToken,
                user: auth.user,  // Pass the AuthUser (CurrentUser | LocalUser)
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
