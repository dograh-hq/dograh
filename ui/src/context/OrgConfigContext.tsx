'use client';

import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';

import { client } from '@/client/client.gen';
import { getCurrentOrganizationContextApiV1OrganizationsContextGet, getUserConfigurationsApiV1UserConfigurationsUserGet, updateUserConfigurationsApiV1UserConfigurationsUserPut } from '@/client/sdk.gen';
import type { OrganizationContextResponse, UserConfigurationRequestResponseSchema } from '@/client/types.gen';
import { setupAuthInterceptor } from '@/lib/apiClient';
import type { AuthUser } from '@/lib/auth';
import { useAuth } from '@/lib/auth';

interface TeamPermission {
    id: string;
}

interface OrganizationPricing {
    price_per_second_usd: number | null;
    currency: string;
    billing_enabled: boolean;
}

interface OrgConfigContextType {
    orgContext: OrganizationContextResponse | null;
    userConfig: UserConfigurationRequestResponseSchema | null;
    saveUserConfig: (userConfig: UserConfigurationRequestResponseSchema) => Promise<void>;
    loading: boolean;
    error: Error | null;
    refreshConfig: () => Promise<void>;
    permissions: TeamPermission[];
    user: AuthUser | null;
    organizationPricing: OrganizationPricing | null;
}

const OrgConfigContext = createContext<OrgConfigContextType | null>(null);

const pricingFromUserConfig = (
    userConfig: UserConfigurationRequestResponseSchema,
): OrganizationPricing | null => {
    if (!userConfig.organization_pricing) {
        return null;
    }

    return {
        price_per_second_usd: userConfig.organization_pricing.price_per_second_usd as number | null,
        currency: (userConfig.organization_pricing.currency as string) || 'USD',
        billing_enabled: (userConfig.organization_pricing.billing_enabled as boolean) || false,
    };
};

export function OrgConfigProvider({ children }: { children: ReactNode }) {
    const [orgContext, setOrgContext] = useState<OrganizationContextResponse | null>(null);
    const [userConfig, setUserConfig] = useState<UserConfigurationRequestResponseSchema | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<Error | null>(null);
    const [organizationPricing, setOrganizationPricing] = useState<OrganizationPricing | null>(null);
    const [permissions, setPermissions] = useState<TeamPermission[]>([]);

    const auth = useAuth();

    const authRef = useRef(auth);
    authRef.current = auth;

    const hasFetchedConfig = useRef(false);
    const hasFetchedPermissions = useRef(false);

    if (!auth.loading && auth.isAuthenticated) {
        setupAuthInterceptor(client, auth.getAccessToken);
    }

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

    const fetchConfig = useCallback(async () => {
        const currentAuth = authRef.current;
        if (!currentAuth.isAuthenticated) {
            return;
        }

        setLoading(true);
        try {
            const [orgContextResponse, userConfigResponse] = await Promise.all([
                getCurrentOrganizationContextApiV1OrganizationsContextGet(),
                getUserConfigurationsApiV1UserConfigurationsUserGet(),
            ]);

            if (orgContextResponse.data) {
                setOrgContext(orgContextResponse.data);
            }

            if (userConfigResponse.data) {
                setUserConfig(userConfigResponse.data);
                setOrganizationPricing(pricingFromUserConfig(userConfigResponse.data));
            }

            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err : new Error('Failed to fetch organization configuration'));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (auth.loading || !auth.isAuthenticated || hasFetchedConfig.current) {
            return;
        }
        hasFetchedConfig.current = true;
        fetchConfig();
    }, [auth.loading, auth.isAuthenticated, fetchConfig]);

    const saveUserConfig = useCallback(async (userConfigRequest: UserConfigurationRequestResponseSchema) => {
        if (!authRef.current.isAuthenticated) throw new Error('No authentication available');
        const response = await updateUserConfigurationsApiV1UserConfigurationsUserPut({
            body: {
                ...userConfig,
                ...userConfigRequest,
            } as UserConfigurationRequestResponseSchema,
        });
        if (response.error) {
            let msg = 'Failed to save user configuration';
            const detail = (response.error as unknown as { detail?: string | { errors: { model: string; message: string }[] } }).detail;
            if (typeof detail === 'string') {
                msg = detail;
            } else if (Array.isArray(detail)) {
                msg = detail
                    .map((e: { model: string; message: string }) => `${e.model}: ${e.message}`)
                    .join('\n');
            }
            throw new Error(msg);
        }

        if (response.data) {
            setUserConfig(response.data);
            setOrganizationPricing(pricingFromUserConfig(response.data));
        }
    }, [userConfig]);

    const refreshConfig = useCallback(async () => {
        await fetchConfig();
    }, [fetchConfig]);

    return (
        <OrgConfigContext.Provider
            value={{
                orgContext,
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
        </OrgConfigContext.Provider>
    );
}

export function useOrgConfig() {
    const context = useContext(OrgConfigContext);
    if (!context) {
        throw new Error('useOrgConfig must be used within an OrgConfigProvider');
    }
    return context;
}
